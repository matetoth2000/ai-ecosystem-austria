"""Describe frozen company scores against the independent-validation selection labels."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ai_ecosystem.calibration.compare_ai_profile_budgets import batch_cost


RUN = Path("data/interim/calibration/ai_profile_validation_batch_v2_final")
REVIEW = Path("data/interim/calibration/ai_profile_validation_failure_review_v5")
SCORES = Path("data/interim/calibration/company_scores/independent_validation_v1.csv")
OUTPUT = Path("data/interim/calibration/company_score_analysis/independent_validation_selection_v3")
MODELS = ("reference_logistic_size", "cleaned_grounded_combined")
CANDIDATES = {MODELS[0]: (0.0, 2.0, 4.0), MODELS[1]: (0.0, 1.0, 2.0)}
LABELS = ("KEEP", "UNKNOWN", "EXCLUDE", "UNRESOLVED")
COLORS = {"KEEP": "#127b57", "UNKNOWN": "#c18510", "EXCLUDE": "#687780", "UNRESOLVED": "#ad3152"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(success: int, count: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    """Descriptive 95% iid-binomial interval, not a population-valid interval."""
    if count == 0:
        return None, None
    p = success / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    spread = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return max(0.0, center - spread), min(1.0, center + spread)


def summarize(selected: list[dict], *, total_keep: int = 52, total_unresolved: int = 19) -> dict:
    counts = Counter(row["analysis_label"] for row in selected)
    keep, unresolved = counts["KEEP"], counts["UNRESOLVED"]
    labelled = keep + counts["UNKNOWN"] + counts["EXCLUDE"]
    total = labelled + unresolved
    interval = wilson(keep, labelled)
    return {
        "selected_total": total, "selected_labelled": labelled, "selected_unresolved": unresolved,
        "selected_keep": keep, "selected_unknown": counts["UNKNOWN"], "selected_exclude": counts["EXCLUDE"],
        "keep_precision_labelled": keep / labelled if labelled else None,
        "precision_wilson95_low": interval[0], "precision_wilson95_high": interval[1],
        "observed_keep_recall": keep / total_keep,
        "precision_missing_low": keep / total if total else None,
        "precision_missing_high": (keep + unresolved) / total if total else None,
        "recall_missing_low": keep / (total_keep + total_unresolved - unresolved),
        "recall_missing_high": (keep + unresolved) / (total_keep + unresolved),
        "estimated_batch_input_usd": sum(row["estimated_batch_input_usd"] for row in selected),
        "estimated_batch_output_reserved_usd": sum(row["estimated_batch_output_reserved_usd"] for row in selected),
        "estimated_batch_total_reserved_usd": sum(row["estimated_batch_total_reserved_usd"] for row in selected),
    }


def build_rows(run: Path = RUN, review: Path = REVIEW, scores_path: Path = SCORES) -> tuple[list[dict], dict]:
    manifest = [r for r in read_jsonl(run / "company_manifest.jsonl")
                if r.get("status") == "included" and r.get("split") == "selection"]
    ids = [r["company_id"] for r in manifest]
    if len(ids) != 865 or len(set(ids)) != 865:
        raise ValueError("Selection manifest membership is not exactly 865 unique companies")
    selection = {}
    for path in sorted((run / "selection_results").glob("*.json")):
        result = json.loads(path.read_text())
        if result["split"] != "selection" or path.stem != result["company_id"]:
            raise ValueError("Nonselection or mismatched result in selection directory")
        selection[result["company_id"]] = result
    if set(selection) != set(ids):
        raise ValueError("Selection results and frozen manifest disagree")
    recovered_rows = read_jsonl(review / "recovered_results.jsonl")
    recovered = {r["company_id"]: r for r in recovered_rows}
    if len(recovered) != 3 or len(recovered_rows) != 3 or not set(recovered) <= set(ids):
        raise ValueError("Recovered-label membership or uniqueness mismatch")
    for cid, recovered_row in recovered.items():
        original_path = run / "selection_results" / f"{cid}.json"
        if (sha256(original_path) != recovered_row["source_result_sha256"] or
                selection[cid]["status"] == "valid" or recovered_row["validation_status"] != "valid" or
                recovered_row["split"] != "selection" or
                recovered_row["request_hash"] != selection[cid]["request_hash"]):
            raise ValueError(f"Recovered provenance mismatch: {cid}")
    score_rows = read_csv(scores_path)
    scores = {r["bvd_id"]: r for r in score_rows}
    if len(scores) != len(score_rows):
        raise ValueError("Duplicate company IDs in frozen scores")
    config = json.loads((run / "config_snapshot.json").read_text())
    output = []
    for meta in manifest:
        cid = meta["company_id"]
        score = scores.get(cid)
        if not score or score["state"] != "PROCESSABLE" or any(not score[name] for name in MODELS):
            raise ValueError(f"Missing frozen score for selection company {cid}")
        result = selection[cid]
        if result["status"] == "valid":
            label, source = result["derived"]["label"], "original_valid"
        elif cid in recovered:
            label, source = recovered[cid]["derived"]["label"], "recovered_review_v5"
        else:
            label, source = "UNRESOLVED", "invalid_unresolved"
        work = json.loads((run / "work" / f"{cid}.json").read_text())
        if work["split"] != "selection" or work["request_hash"] != result["request_hash"]:
            raise ValueError(f"Frozen request mismatch: {cid}")
        input_tokens = int(work["estimated_input_tokens"])
        cost_input, cost_output = batch_cost(input_tokens, config["max_output_tokens"], config)
        output.append({"company_id": cid, "company_name": json.loads(work["batch_line"]["body"]["input"][-1]["content"])["company_name"],
                       "group_id": meta["group_id"], "website_id": meta["website_id"], "sector": meta["sector"],
                       "analysis_label": label, "label_source": source, "original_validation_status": result["status"],
                       "reference_logistic_size": float(score[MODELS[0]]),
                       "cleaned_grounded_combined": float(score[MODELS[1]]),
                       "estimated_input_tokens": input_tokens, "estimated_batch_input_usd": cost_input,
                       "estimated_batch_output_reserved_usd": cost_output,
                       "estimated_batch_total_reserved_usd": cost_input + cost_output,
                       "request_hash": result["request_hash"]})
    output.sort(key=lambda row: row["company_id"])
    if Counter(row["analysis_label"] for row in output) != {
            "KEEP": 52, "UNKNOWN": 60, "EXCLUDE": 734, "UNRESOLVED": 19}:
        raise ValueError("Selection analysis label census differs from review")
    if Counter(row["label_source"] for row in output) != {
            "original_valid": 843, "recovered_review_v5": 3, "invalid_unresolved": 19}:
        raise ValueError("Selection analysis provenance census differs from review")
    return output, config


def curve(rows: list[dict], model: str) -> list[dict]:
    ordered = sorted(rows, key=lambda row: (-row[model], row["company_id"]))
    values = []
    cutoff_scores = sorted({row[model] for row in rows}, reverse=True)
    for cutoff in cutoff_scores:
        selected = [row for row in ordered if row[model] >= cutoff]
        values.append({"model": model, "cutoff": cutoff, "rule": "score >= cutoff; all ties included",
                       **summarize(selected)})
    return values


def candidate(rows: list[dict], model: str, cutoff: float) -> dict:
    selected = [row for row in rows if row[model] >= cutoff]
    return {"model": model, "cutoff": cutoff, "rule": "score >= cutoff; all ties included", **summarize(selected)}


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot_distributions(rows: list[dict], model: str, output: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(10, 6), sharex=True)
    scores = [row[model] for row in rows]
    bins = np.linspace(min(scores), max(scores), 32)
    for ax, label in zip(axes.flat, LABELS, strict=True):
        values = [row[model] for row in rows if row["analysis_label"] == label]
        ax.hist(values, bins=bins, color=COLORS[label], alpha=0.85)
        ax.set_title(f"{label} (n={len(values)})")
        ax.set_ylabel("Companies")
        ax.grid(axis="y", alpha=0.18)
    for ax in axes[-1]:
        ax.set_xlabel("Frozen model score (not a probability)")
    fig.suptitle(f"Selection-company score distribution: {model}")
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)


def plot_curves(curves: list[dict], model: str, output: Path) -> None:
    x = np.array([row["cutoff"] for row in curves])
    order = np.argsort(x)
    sorted_rows = [curves[index] for index in order]
    x = x[order]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    ax = axes[0, 0]
    for key, color, label in (("keep_precision_labelled", COLORS["KEEP"], "Confirmed KEEP precision"),
                              ("observed_keep_recall", "#245caa", "Observed KEEP recall")):
        ax.plot(x, [r[key] if r[key] is not None else np.nan for r in sorted_rows], label=label, color=color)
    ax.set_ylabel("Share of labelled companies / 52 KEEPs")
    ax.set_ylim(0, 1.02)
    ax.legend(fontsize=8)
    ax = axes[0, 1]
    for label in ("KEEP", "UNKNOWN", "EXCLUDE"):
        ax.plot(x, [r[f"selected_{label.lower()}"] for r in sorted_rows], label=label, color=COLORS[label])
    ax.set_ylabel("Selected labelled companies")
    ax.legend(fontsize=8)
    ax = axes[1, 0]
    ax.plot(x, [r["selected_total"] for r in sorted_rows], color="#245caa", label="Companies selected")
    twin = ax.twinx()
    twin.plot(x, [r["estimated_batch_total_reserved_usd"] for r in sorted_rows], color="#b4691b", label="Estimated Batch cost")
    ax.set_ylabel("Companies selected")
    twin.set_ylabel("Estimated Batch USD, full 8k output reserve")
    ax = axes[1, 1]
    ax.plot(x, [r["selected_unresolved"] for r in sorted_rows], color=COLORS["UNRESOLVED"])
    ax.set_ylabel("Unresolved companies selected")
    for ax in axes.flat:
        ax.set_xlabel("Score cutoff (score >= cutoff; ties included)")
        ax.grid(alpha=0.18)
        for cutoff in CANDIDATES[model]:
            ax.axvline(cutoff, color="#323232", linestyle=":", linewidth=0.8)
    for ax in axes[0]:
        for cutoff in CANDIDATES[model]:
            ax.annotate(f"{cutoff:g}", xy=(cutoff, 1), xycoords=("data", "axes fraction"),
                        xytext=(2, -4), textcoords="offset points", fontsize=7,
                        va="top", color="#323232")
    fig.suptitle(f"Selection-set cutoff tradeoffs: {model}")
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)


def format_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def report(rows: list[dict], candidates: list[dict], equal: list[dict], output: Path) -> None:
    lines = ["# Independent-validation selection-set scorer comparison", "",
             "This is a descriptive analysis of the 865 selection companies only. It is not weighted to the Austrian firm population; holdout labels were not opened.",
             "The labels combine 843 originally valid Batch responses with three separately revalidated wrong-page-ID corrections from the 5.5a review. Nineteen responses remain unresolved. Original results and frozen model scores are unchanged.",
             "", "## Label view and interpretation", "",
             "846 labelled: **52 KEEP, 60 UNKNOWN, 734 EXCLUDE**; 19 unresolved. All 865 have both frozen scores.",
             "Scores are ranking values, **not probabilities**. Confirmed-KEEP precision = selected KEEP / all labelled selected, including UNKNOWN. Observed KEEP recall = selected KEEP / 52 labelled KEEPs.",
             "Missing-label precision bounds make all selected unresolved non-KEEP versus KEEP. Missing-label recall bounds allow unresolved companies outside versus inside the shortlist to be KEEP. These are extreme missing-label bounds, **not confidence intervals**.",
             "The separate 95% Wilson interval for labelled precision assumes iid binomial labelled selected companies; shared websites, enriched selection and nonrandom missing labels violate population inference, so use it only as a descriptive uncertainty aid.",
             "", "## Candidate cutoffs", "",
             "Every option uses **score >= cutoff**, with all score ties included. Costs are saved final-request local input-token estimates plus the full 8,000-token output reservation at frozen Luna Batch prices; they are not verified bills or final-pass production cost.",
             "", "| Model | Cutoff | Selected (labelled + unresolved) | KEEP / UNKNOWN / EXCLUDE | KEEP precision (Wilson 95%) | Observed recall | Precision missing-label bounds | Recall missing-label bounds | Estimated Batch USD |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in candidates:
        lines.append(f"| `{row['model']}` | {row['cutoff']:g} | {row['selected_total']} ({row['selected_labelled']} + {row['selected_unresolved']}) | "
                     f"{row['selected_keep']} / {row['selected_unknown']} / {row['selected_exclude']} | "
                     f"{format_pct(row['keep_precision_labelled'])} ({format_pct(row['precision_wilson95_low'])}-{format_pct(row['precision_wilson95_high'])}) | "
                     f"{format_pct(row['observed_keep_recall'])} | "
                     f"{format_pct(row['precision_missing_low'])}-{format_pct(row['precision_missing_high'])} | "
                     f"{format_pct(row['recall_missing_low'])}-{format_pct(row['recall_missing_high'])} | "
                     f"${row['estimated_batch_total_reserved_usd']:.3f} |")
    lines.extend(["", "Historical cutoff regions informed these options; none is adopted as a production cutoff. High precision at a very small selected count must not be mistaken for reliable population yield.",
                  "At these candidate cutoffs, neither model demonstrates convincing high confirmed-KEEP precision at a sizable selection: the strictest reference option is 16 KEEP among 28 labelled, and the strictest challenger option is 24 among 47. Broader options capture more KEEPs but also admit many EXCLUDEs.",
                  "", "## Equal shortlist sizes", "",
                  "Top-K is shown only as a descriptive equal-budget comparison; ties are broken deterministically by company ID. Cutoff tables above do not break ties.",
                  "", "| Model | Top K | KEEP / UNKNOWN / EXCLUDE / unresolved | Confirmed KEEP precision | Observed KEEP recall |",
                  "|---|---:|---:|---:|---:|"])
    for row in equal:
        lines.append(f"| `{row['model']}` | {row['top_k']} | {row['selected_keep']} / {row['selected_unknown']} / {row['selected_exclude']} / {row['selected_unresolved']} | "
                     f"{format_pct(row['keep_precision_labelled'])} | {format_pct(row['observed_keep_recall'])} |")
    lines.extend(["", "At equal top-100 size, the combined challenger finds 42 of 52 observed KEEPs versus 37 for the reference. At top 25 the reference finds 15 versus 13; top 50 is tied at 24. These are selection-set comparisons only, not proof of a population winner."])
    for model in MODELS:
        lines.extend(["", f"## {model}", "",
                      f"![Label-specific score distributions]({model}_distributions.png)", "",
                      "Separate panels keep the much larger EXCLUDE group from hiding KEEP and UNKNOWN distributions. The unresolved panel is not treated as a fourth relevance label.", "",
                      f"![Cutoff curves]({model}_curves.png)", "",
                      "Dotted lines mark the three candidate cutoffs. Moving left admits more companies and raises the estimated Batch reservation; the unresolved count is shown separately."])
    unresolved = [row for row in rows if row["analysis_label"] == "UNRESOLVED"]
    material = []
    for row in unresolved:
        selected_by = [f"{model}@{cutoff:g}" for model in MODELS for cutoff in CANDIDATES[model]
                       if row[model] >= cutoff]
        if selected_by:
            material.append((row, selected_by))
    lines.extend(["", "## Unresolved cases near candidate decisions", "",
                  "These cases can widen the displayed bounds; resolving them may change the preferred cutoff. This table is triage, not a retry request.", "",
                  "| Company ID | Reference score | Challenger score | Included at |",
                  "|---|---:|---:|---|"])
    for row, selected_by in material:
        lines.append(f"| {row['company_id']} | {row[MODELS[0]]:.3f} | {row[MODELS[1]]:.3f} | {', '.join(selected_by)} |")
    lines.extend(["", "Only three unresolved companies enter any displayed candidate shortlist. `AT9110970290` is selected by every nonzero candidate cutoff and is highest priority for reference/contract resolution. `AT9030020258` matters more to the reference options, while `AT9130109951` matters more to the challenger options. Their outcomes could affect close cutoff comparisons, but cannot alone establish a high-precision operating point."])
    lines.extend(["", "## Limits", "",
                  "This pool was acquired through a broad-sector design, but website evidence availability and unresolved labels limit inference. These are unweighted, descriptive selection-set measurements, not Austrian prevalence, population recall or population spend. The frozen models were not retrained and no holdout labels were used.",
                  "The local token counts and saved Batch price schedule (checked 2026-10-13 in the frozen config) estimate a **same-setup Luna profile pass** for these shortlists under the 250k-evidence/8k-output limits. These are cost proxies, not actual bills; if the eventual final-pass contract differs, deployment budgeting must be redone.",
                  "", "Supporting files: `company_analysis.csv`, `cutoff_curves.csv`, `candidate_cutoffs.csv`, `equal_budget.csv`, `analysis_manifest.json`.", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def build(output: Path = OUTPUT) -> dict:
    if output.exists():
        raise FileExistsError(output)
    rows, config = build_rows()
    curves = [point for model in MODELS for point in curve(rows, model)]
    candidates = [candidate(rows, model, cutoff) for model in MODELS for cutoff in CANDIDATES[model]]
    equal = []
    for model in MODELS:
        ordered = sorted(rows, key=lambda row: (-row[model], row["company_id"]))
        for top_k in (25, 50, 100):
            equal.append({"model": model, "top_k": top_k, **summarize(ordered[:top_k])})
    output.mkdir(parents=True)
    write_csv(output / "company_analysis.csv", rows)
    write_csv(output / "cutoff_curves.csv", curves)
    write_csv(output / "candidate_cutoffs.csv", candidates)
    write_csv(output / "equal_budget.csv", equal)
    for model in MODELS:
        plot_distributions(rows, model, output / f"{model}_distributions.png")
        plot_curves([row for row in curves if row["model"] == model], model, output / f"{model}_curves.png")
    report(rows, candidates, equal, output)
    manifest = {"analysis_version": "independent_validation_selection_v3", "selection_count": len(rows),
                "label_counts": dict(Counter(row["analysis_label"] for row in rows)),
                "label_source_counts": dict(Counter(row["label_source"] for row in rows)),
                "score_source_sha256": sha256(SCORES),
                "selection_manifest_sha256": sha256(RUN / "company_manifest.jsonl"),
                "review_recovered_sha256": sha256(REVIEW / "recovered_results.jsonl"),
                "frozen_config_sha256": sha256(RUN / "config_snapshot.json"),
                "pricing": config["pricing"], "cost_basis": "saved frozen requests; local input-token estimate; full 8k output reservation",
                "candidate_cutoffs": CANDIDATES, "tie_rule": "score >= cutoff, include all ties"}
    (output / "analysis_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))


if __name__ == "__main__":
    main()
