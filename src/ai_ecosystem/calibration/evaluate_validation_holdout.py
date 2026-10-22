"""Evaluate three pre-frozen company-score policies on the validation holdout."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .analyze_validation_selection import read_csv, read_jsonl, sha256, wilson
from .analyze_validation_usage_costs import selection_usage, usage_cost
from .company_ai_profile_contract import derive_label, validate_profile
from .review_validation_failures import review_one, selected_raw_responses


ROOT = Path("data/interim/calibration")
RUN = ROOT / "ai_profile_validation_batch_v2_final"
SCORES = ROOT / "company_scores/independent_validation_v1.csv"
SELECTION = ROOT / "company_score_analysis/independent_validation_selection_v3/company_analysis.csv"
CURVE = ROOT / "company_score_analysis/independent_validation_usage_costs_v2/conditional_population_scenarios.csv"
OUTPUT = ROOT / "company_score_analysis/independent_validation_holdout_v1"
MODEL = "cleaned_grounded_combined"
COLORS = {"KEEP": "#127b57", "UNKNOWN": "#c18510", "EXCLUDE": "#687780", "UNRESOLVED": "#ad3152"}


def verify_freeze(output: Path, run: Path = RUN, scores: Path = SCORES, curve: Path = CURVE) -> tuple[dict, str]:
    path = output / "policy_freeze.json"
    freeze_hash = sha256(path)
    freeze = json.loads(path.read_text(encoding="utf-8"))
    paths = {
        "model_sha256": ROOT / "company_model_experiments/company_ai_relevance_cleanup_v3/frozen/cleaned_grounded_combined/model.joblib",
        "score_sha256": scores,
        "split_manifest_sha256": run / "company_manifest.jsonl",
        "preparation_identity_sha256": run / "preparation_identity.json",
        "config_sha256": run / "config_snapshot.json",
        "schema_sha256": run / "schema_snapshot.json",
        "prompt_sha256": run / "prompt_snapshot.txt",
        "selection_analysis_sha256": ROOT / "company_score_analysis/independent_validation_selection_v3/analysis_manifest.json",
        "selection_cost_curve_sha256": curve,
        "selection_usage_sha256": ROOT / "company_score_analysis/independent_validation_usage_costs_v2/company_usage_costs.csv",
    }
    if any(sha256(path) != freeze[key] for key, path in paths.items()):
        raise ValueError("Frozen policy/source hash mismatch")
    config = json.loads((run / "config_snapshot.json").read_text())
    if (freeze["model"] != MODEL or freeze["score_rule"] != "score >= cutoff; include all ties"
            or config["contract_id"] != freeze["label_contract"]
            or config["mapping_version"] != freeze["mapping_version"]
            or config["model"] != "gpt-6-luna"
            or [p["budget_usd"] for p in freeze["policies"]] != [100, 150, 200]):
        raise ValueError("Frozen policy/contract mismatch")
    rows = read_csv(curve)
    for policy in freeze["policies"]:
        matches = [r for r in rows if r["model"] == MODEL and float(r["cutoff"]) == policy["cutoff"]]
        if len(matches) != 1 or int(matches[0]["selected_keep"]) != policy["selection_keep"] or int(matches[0]["selected_total"]) != policy["selection_selected"]:
            raise ValueError("Frozen threshold is not uniquely identified by saved selection curve")
    return freeze, freeze_hash


def holdout_rows(freeze: dict, run: Path = RUN, scores_path: Path = SCORES) -> tuple[list[dict], list[dict], list[dict], dict]:
    members = [r for r in read_jsonl(run / "company_manifest.jsonl") if r.get("status") == "included"]
    if Counter(r["split"] for r in members) != freeze["split_counts"]:
        raise ValueError("Validation split membership changed")
    if any(len({r["split"] for r in members if r["group_id"] == group}) != 1 for group in {r["group_id"] for r in members}):
        raise ValueError("Website group crosses selection/holdout")
    holdout = [r for r in members if r["split"] == "holdout"]
    ids = {r["company_id"] for r in holdout}
    if len(ids) != 216:
        raise ValueError("Duplicate/missing holdout companies")
    score_rows = read_csv(scores_path)
    scores = {r["bvd_id"]: r for r in score_rows}
    if len(scores) != len(score_rows):
        raise ValueError("Duplicate scorer row")
    paths = sorted((run / "holdout_results").glob("*.json"))
    if len(paths) != 216 or {p.stem for p in paths} != ids:
        raise ValueError("Holdout result membership mismatch")
    results = {p.stem: json.loads(p.read_text()) for p in paths}
    invalid = {r["custom_id"] for r in results.values() if r["status"] != "valid"}
    raw = selected_raw_responses(run, invalid) if invalid else {}
    schema = json.loads((run / "schema_snapshot.json").read_text())
    config = json.loads((run / "config_snapshot.json").read_text())
    records, diagnostics, recovered = [], [], []
    custom_ids = set()
    for meta in sorted(holdout, key=lambda r: r["company_id"]):
        cid = meta["company_id"]
        result = results[cid]
        work_path = run / "work" / f"{cid}.json"
        work = json.loads(work_path.read_text())
        score = scores.get(cid)
        if (result["company_id"] != cid or result["split"] != "holdout" or work["split"] != "holdout"
                or result["custom_id"] in custom_ids or result["request_hash"] != work["request_hash"]
                or score is None or score["state"] != "PROCESSABLE" or not score[MODEL]
                or work["group_id"] != meta["group_id"]
                or result["quote_validation_version"] != freeze["quote_validation_version"]):
            raise ValueError(f"Holdout join/contract mismatch: {cid}")
        custom_ids.add(result["custom_id"])
        package = json.loads(work["batch_line"]["body"]["input"][-1]["content"])
        if package["company_id"] != cid:
            raise ValueError(f"Frozen request company mismatch: {cid}")
        if result["status"] == "valid":
            validated = validate_profile(result["profile"], package, schema, contract_id=config["contract_id"])
            derived = derive_label(validated, mapping_version=config["mapping_version"])
            if derived != result["derived"]:
                raise ValueError(f"Saved valid label differs from approved mapping: {cid}")
            label, source = derived["label"], "original_valid"
        else:
            if result["custom_id"] not in raw:
                raise ValueError(f"Missing raw invalid response: {cid}")
            body, raw_hash = raw[result["custom_id"]]
            diagnostic, correction = review_one(result, work, schema, config, sha256(run / "holdout_results" / f"{cid}.json"),
                                                body, raw_hash, expected_split="holdout")
            diagnostics.append(diagnostic)
            if correction:
                recovered.append(correction)
                label, source = correction["derived"]["label"], "recovered_mechanical"
            else:
                label, source = "UNRESOLVED", "invalid_unresolved"
        usage, returned_model = selection_usage(run / "holdout_results" / f"{cid}.json")
        if usage is None or returned_model != "gpt-6-luna":
            raise ValueError(f"Missing usage/model mismatch: {cid}")
        cost = usage_cost(usage)
        records.append({"company_id": cid, "group_id": meta["group_id"], "website_id": meta["website_id"],
                        "sector": meta["sector"], "score": float(score[MODEL]), "label": label, "label_source": source,
                        "original_status": result["status"], "request_hash": result["request_hash"],
                        "source_result_sha256": sha256(run / "holdout_results" / f"{cid}.json"),
                        **cost})
    if len(records) != 216 or len(diagnostics) != len(invalid):
        raise ValueError("Incomplete holdout audit")
    return records, diagnostics, recovered, {"result_hashes": {p.stem: sha256(p) for p in paths},
                                               "work_hashes": {cid: sha256(run / "work" / f"{cid}.json") for cid in ids}}


def metrics(rows: list[dict], cutoff: float) -> dict:
    selected = [r for r in rows if r["score"] >= cutoff]
    counts = Counter(r["label"] for r in selected)
    total = Counter(r["label"] for r in rows)
    keep, unresolved = counts["KEEP"], counts["UNRESOLVED"]
    labelled = len(selected) - unresolved
    precision = keep / labelled if labelled else None
    recall = keep / total["KEEP"] if total["KEEP"] else None
    recall_low_den = total["KEEP"] + total["UNRESOLVED"] - unresolved
    recall_high_den = total["KEEP"] + unresolved
    interval = wilson(keep, labelled)
    return {"cutoff": cutoff, "selected_total": len(selected), "selected_labelled": labelled,
            "selected_keep": keep, "selected_unknown": counts["UNKNOWN"], "selected_exclude": counts["EXCLUDE"],
            "selected_unresolved": unresolved, "total_keep": total["KEEP"], "total_unresolved": total["UNRESOLVED"],
            "confirmed_keep_precision": precision, "observed_keep_recall": recall,
            "precision_missing_low": keep / len(selected) if selected else None,
            "precision_missing_high": (keep + unresolved) / len(selected) if selected else None,
            "recall_missing_low": keep / recall_low_den if recall_low_den else None,
            "recall_missing_high": (keep + unresolved) / recall_high_den if recall_high_den else None,
            "precision_wilson95_low": interval[0], "precision_wilson95_high": interval[1],
            "selected_input_tokens": sum(r["input_tokens"] for r in selected),
            "selected_output_tokens_including_reasoning": sum(r["output_tokens"] for r in selected),
            "selected_reasoning_tokens_included": sum(r["reasoning_output_tokens_included"] for r in selected),
            "selected_input_usd": sum(r["input_cost_usd"] for r in selected),
            "selected_output_usd": sum(r["output_cost_usd"] for r in selected),
            "selected_total_usd": sum(r["total_cost_usd"] for r in selected)}


def group_intervals(rows: list[dict], policies: list[dict], seed: int, replicates: int) -> dict[int, dict]:
    by_group = defaultdict(list)
    for row in rows:
        by_group[row["group_id"]].append(row)
    groups = [by_group[key] for key in sorted(by_group)]
    rng = np.random.default_rng(seed)
    values = {p["budget_usd"]: {"precision": [], "recall": []} for p in policies}
    for _ in range(replicates):
        sampled = [r for i in rng.integers(0, len(groups), size=len(groups)) for r in groups[i]]
        for policy in policies:
            result = metrics(sampled, policy["cutoff"])
            for measure, field in (("precision", "confirmed_keep_precision"), ("recall", "observed_keep_recall")):
                if result[field] is not None:
                    values[policy["budget_usd"]][measure].append(result[field])
    return {budget: {f"{measure}_group_bootstrap95_{bound}": float(np.quantile(items[measure], q)) if items[measure] else None
                     for measure in ("precision", "recall") for bound, q in (("low", .025), ("high", .975))}
            for budget, items in values.items()}


def write_csv(path: Path, rows: list[dict]) -> None:
    if path.exists():
        raise FileExistsError(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def pct(value) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def charts(results: list[dict], output: Path) -> None:
    x = np.arange(len(results))
    labels = [f"${r['budget_usd']}" for r in results]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.3))
    for ax, key, title in ((axes[0], "confirmed_keep_precision", "Confirmed KEEP precision"),
                           (axes[1], "observed_keep_recall", "Observed KEEP recall")):
        for index, (name, color) in enumerate((("selection", "#245caa"), ("holdout", "#127b57"))):
            center = x + (-.16 if index == 0 else .16)
            values = [r[f"{name}_{key}"] for r in results]
            lower = [r.get(f"{name}_{'precision' if 'precision' in key else 'recall'}_group_bootstrap95_low") for r in results]
            upper = [r.get(f"{name}_{'precision' if 'precision' in key else 'recall'}_group_bootstrap95_high") for r in results]
            ax.bar(center, values, width=.31, color=color, label=name.title())
            ax.errorbar(center, values, yerr=[[max(0, v - l) if l is not None else 0 for v, l in zip(values, lower)],
                                               [max(0, h - v) if h is not None else 0 for v, h in zip(values, upper)]],
                        fmt="none", ecolor="#202a2e", capsize=3)
            for j, value in enumerate(values):
                ax.text(center[j], min(1.02, value + .075), f"{pct(value)}\n({r_count(results[j], name, key)})", ha="center", fontsize=7)
        ax.set_title(title)
        ax.set_xticks(x, labels)
        ax.set_ylim(0, 1.17)
        ax.grid(axis="y", alpha=.2)
    axes[0].set_ylabel("Share of labelled cases")
    axes[0].legend(loc="upper right")
    axes[1].set_ylabel("Share of labelled KEEPs")
    fig.suptitle("Frozen policies: selection versus holdout (group-bootstrap bars)")
    fig.tight_layout()
    fig.savefig(output / "precision_recall.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    bottom = np.zeros(len(results))
    for label in ("KEEP", "UNKNOWN", "EXCLUDE", "UNRESOLVED"):
        values = np.array([r[f"holdout_selected_{label.lower()}"] for r in results])
        axes[0].bar(labels, values, bottom=bottom, color=COLORS[label], label=label)
        bottom += values
    axes[0].set_title("Holdout shortlist composition")
    axes[0].set_ylabel("Companies selected")
    axes[0].legend()
    inputs = np.array([r["holdout_selected_input_usd"] for r in results])
    outputs = np.array([r["holdout_selected_output_usd"] for r in results])
    axes[1].bar(labels, inputs, label="Input", color="#245caa")
    axes[1].bar(labels, outputs, bottom=inputs, label="Output (includes reasoning)", color="#c18510")
    axes[1].set_title("Observed holdout Batch usage cost")
    axes[1].set_ylabel("Calculated USD, not confirmed billing")
    axes[1].legend()
    for ax in axes:
        ax.grid(axis="y", alpha=.2)
    fig.tight_layout()
    fig.savefig(output / "composition_cost.png", dpi=160)
    plt.close(fig)


def r_count(row: dict, split: str, metric: str) -> str:
    numerator = row[f"{split}_selected_keep"]
    denominator = row[f"{split}_selected_labelled"] if "precision" in metric else row[f"{split}_total_keep"]
    return f"{numerator}/{denominator}"


def selection_cost_rows(selection: list[dict], usage_path: Path) -> list[dict]:
    usage_rows = read_csv(usage_path)
    usage = {r["company_id"]: r for r in usage_rows}
    if len(usage) != len(usage_rows) or set(usage) != {r["company_id"] for r in selection}:
        raise ValueError("Saved selection usage does not cover the selection analysis exactly")
    rows = []
    for row in selection:
        saved = usage[row["company_id"]]
        if (saved["analysis_label"] != row["analysis_label"] or
                saved["label_source"] != row["label_source"] or
                float(saved[MODEL]) != float(row[MODEL])):
            raise ValueError("Saved selection usage/label/score mismatch")
        rows.append({"company_id": row["company_id"], "group_id": row["group_id"],
                     "label": row["analysis_label"], "score": float(row[MODEL]),
                     **{key: float(saved[key]) for key in ("input_tokens", "output_tokens", "reasoning_output_tokens_included",
                                                            "input_cost_usd", "output_cost_usd", "total_cost_usd")}})
    return rows


def reconcile_selection_cost(output: Path = OUTPUT) -> None:
    freeze, freeze_hash = verify_freeze(output)
    source = output / "policy_results.csv"
    if not source.exists() or not (output / "report.md").exists():
        raise ValueError("Initial frozen evaluation must exist before reconciliation")
    if any((output / name).exists() for name in ("policy_results_v2.csv", "report_v2.md", "cost_comparison.png")):
        raise FileExistsError("Reconciled evaluation already exists")
    selection = read_csv(SELECTION)
    usage_path = ROOT / "company_score_analysis/independent_validation_usage_costs_v2/company_usage_costs.csv"
    selection_rows = selection_cost_rows(selection, usage_path)
    original = read_csv(source)
    results = []
    for row, policy in zip(original, freeze["policies"], strict=True):
        if int(row["budget_usd"]) != policy["budget_usd"] or float(row["holdout_cutoff"]) != policy["cutoff"]:
            raise ValueError("Frozen report/policy mismatch")
        correct = metrics(selection_rows, policy["cutoff"])
        if correct["selected_total"] != int(row["selection_selected_total"]) or correct["selected_keep"] != int(row["selection_selected_keep"]):
            raise ValueError("Selection cost reconciliation changed label results")
        for key in ("selected_input_tokens", "selected_output_tokens_including_reasoning", "selected_reasoning_tokens_included",
                    "selected_input_usd", "selected_output_usd", "selected_total_usd"):
            row["selection_" + key] = correct[key]
        results.append(row)
    write_csv(output / "policy_results_v2.csv", results)
    x = np.arange(3)
    fig, ax = plt.subplots(figsize=(7, 4.1))
    for offset, prefix, color in ((-.18, "selection", "#245caa"), (.18, "holdout", "#127b57")):
        values = [float(row[prefix + "_selected_total_usd"]) for row in results]
        ax.bar(x + offset, values, width=.34, color=color, label=prefix.title())
        for j, value in enumerate(values):
            ax.text(x[j] + offset, value + .015, f"${value:.3f}", ha="center", fontsize=8)
    ax.set_xticks(x, [f"${p['budget_usd']} policy" for p in freeze["policies"]])
    ax.set_ylabel("Calculated Batch usage USD, not confirmed billing")
    ax.set_title("Selection and holdout request costs at frozen cutoffs")
    ax.set_ylim(0, max(float(r["selection_selected_total_usd"]) for r in results) * 1.16)
    ax.legend()
    ax.grid(axis="y", alpha=.2)
    fig.tight_layout()
    fig.savefig(output / "cost_comparison.png", dpi=160)
    plt.close(fig)
    table = ["## Selection versus holdout usage cost", "",
             "![Calculated request costs](cost_comparison.png)", "",
             "These are costs for the *observed* 865-company selection and 216-company holdout samples, respectively. Different sample sizes explain much of the difference; neither is a population cost projection.", "",
             "| Scenario | Selection selected / input + output = total USD | Holdout selected / input + output = total USD |",
             "|---|---:|---:|"]
    for row in results:
        table.append(f"| ${row['budget_usd']} | {row['selection_selected_total']} / "
                     f"${float(row['selection_selected_input_usd']):.4f} + ${float(row['selection_selected_output_usd']):.4f} = ${float(row['selection_selected_total_usd']):.4f} | "
                     f"{row['holdout_selected_total']} / ${float(row['holdout_selected_input_usd']):.4f} + "
                     f"${float(row['holdout_selected_output_usd']):.4f} = ${float(row['holdout_selected_total_usd']):.4f} |")
    projections = ", ".join(f"${policy['selection_projected_request_cost_usd']:.2f}" for policy in freeze["policies"])
    table.extend(["", f"The pre-evaluation **conditional wider-frame** request-cost projections were {projections} for the $100, $150 and $200 policies, respectively, before reserve. They were derived from the selection set and were not recalibrated using holdout labels or costs. They are distinct from the observed sample costs above and are not guaranteed deployment spending."])
    original_report = (output / "report.md").read_text()
    report = original_report.replace("## Transfer from selection", "\n".join(table) + "\n\n## Transfer from selection")
    report = report.replace("# Frozen challenger policies: independent holdout", "# Frozen challenger policies: independent holdout (cost-reconciled report)", 1)
    holdout_groups = len({r["group_id"] for r in read_csv(output / "holdout_company_metrics.csv")})
    selection_groups = len({r["group_id"] for r in selection})
    report = report.replace("The Wilson precision bounds are in `policy_results.csv` as a separate iid descriptive calculation.",
                            f"The Wilson precision bounds are in `policy_results_v2.csv` as a separate iid descriptive calculation. The holdout has {holdout_groups} distinct website groups; the selection set has {selection_groups} groups among {len(selection)} companies, with no group crossing splits.")
    report += "\nThe initial `policy_results.csv` contains zero placeholders for selection-set costs only; `policy_results_v2.csv` supersedes those columns. Holdout metrics and the pre-evaluation freeze are unchanged.\n"
    (output / "report_v2.md").write_text(report, encoding="utf-8")
    write_json(output / "cost_reconciliation.json", {"freeze_sha256": freeze_hash,
               "initial_policy_results_sha256": sha256(source), "selection_usage_sha256": sha256(usage_path),
               "corrected_policy_results_sha256": sha256(output / "policy_results_v2.csv"),
               "holdout_label_metrics_unchanged": True})


def run(output: Path = OUTPUT) -> dict:
    freeze, freeze_hash = verify_freeze(output)
    expected = ["holdout_company_metrics.csv", "policy_results.csv", "recovery_audit.json", "report.md"]
    if any((output / name).exists() for name in expected):
        raise FileExistsError("Holdout evaluation already exists; preserve prior artifacts")
    holdout, diagnostics, recovered, sources = holdout_rows(freeze)
    selection = read_csv(SELECTION)
    if len(selection) != 865 or Counter(r["analysis_label"] for r in selection) != {"KEEP": 52, "UNKNOWN": 60, "EXCLUDE": 734, "UNRESOLVED": 19}:
        raise ValueError("Selection analysis membership/labels changed")
    selection_rows = selection_cost_rows(selection, ROOT / "company_score_analysis/independent_validation_usage_costs_v2/company_usage_costs.csv")
    policies = freeze["policies"]
    h_intervals = group_intervals(holdout, policies, freeze["metrics"]["bootstrap_seed"], freeze["metrics"]["bootstrap_replicates"])
    s_intervals = group_intervals(selection_rows, policies, freeze["metrics"]["bootstrap_seed"], freeze["metrics"]["bootstrap_replicates"])
    results = []
    for policy in policies:
        budget = policy["budget_usd"]
        h = metrics(holdout, policy["cutoff"])
        s = metrics(selection_rows, policy["cutoff"])
        if s["selected_keep"] != policy["selection_keep"] or s["selected_total"] != policy["selection_selected"]:
            raise ValueError("Frozen selection metrics no longer reproduce")
        results.append({"budget_usd": budget, "planned_request_allowance_usd": budget * (1 - freeze["cost_basis"]["budget_reserve_fraction"]),
                        **{f"selection_{k}": v for k, v in {**s, **s_intervals[budget]}.items()},
                        **{f"holdout_{k}": v for k, v in {**h, **h_intervals[budget]}.items()}})
    if any(results[i]["holdout_selected_total"] > results[i + 1]["holdout_selected_total"] for i in range(2)):
        raise ValueError("Frozen nested cutoff ordering violated")
    write_csv(output / "holdout_company_metrics.csv", holdout)
    write_csv(output / "policy_results.csv", results)
    write_json(output / "recovery_audit.json", diagnostics)  # Separate from original results.
    if recovered:
        write_json(output / "recovered_results.json", recovered)
    summary = {"freeze_sha256": freeze_hash, "holdout_companies": len(holdout),
               "holdout_groups": len({r["group_id"] for r in holdout}),
               "holdout_labels": dict(Counter(r["label"] for r in holdout)),
               "original_valid": sum(r["label_source"] == "original_valid" for r in holdout),
               "recovered": len(recovered), "unresolved": sum(r["label"] == "UNRESOLVED" for r in holdout),
               "holdout_input_tokens": sum(r["input_tokens"] for r in holdout),
               "holdout_output_tokens_including_reasoning": sum(r["output_tokens"] for r in holdout),
               "holdout_calculated_usage_usd": sum(r["total_cost_usd"] for r in holdout),
               "sources": sources}
    write_json(output / "evaluation_manifest.json", summary)
    charts(results, output)
    lines = ["# Frozen challenger policies: independent holdout", "",
             f"Policy frozen before holdout access: `policy_freeze.json` (SHA-256 `{freeze_hash}`). All three policies are reported unchanged. No minimum performance target was specified.",
             f"Holdout: {len(holdout)} companies, {summary['holdout_groups']} website groups; {summary['original_valid']} original-valid, {len(recovered)} mechanically recovered, {summary['unresolved']} unresolved. Original responses were preserved.",
             "", "## Results", "",
             "| Scenario | Score cutoff >= | Holdout selected (labelled) | KEEP / UNKNOWN / EXCLUDE / unresolved | Precision (95% group bootstrap) | Recall (95% group bootstrap) | Missing-label precision / recall bounds | Holdout input + output = total USD |",
             "|---|---:|---:|---|---|---|---|---:|"]
    for r in results:
        lines.append(f"| ${r['budget_usd']} | {r['holdout_cutoff']:.12g} | {r['holdout_selected_total']} ({r['holdout_selected_labelled']}) | "
                     f"{r['holdout_selected_keep']} / {r['holdout_selected_unknown']} / {r['holdout_selected_exclude']} / {r['holdout_selected_unresolved']} | "
                     f"{pct(r['holdout_confirmed_keep_precision'])} ({pct(r['holdout_precision_group_bootstrap95_low'])}-{pct(r['holdout_precision_group_bootstrap95_high'])}) | "
                     f"{r['holdout_selected_keep']}/{r['holdout_total_keep']} = {pct(r['holdout_observed_keep_recall'])} "
                     f"({pct(r['holdout_recall_group_bootstrap95_low'])}-{pct(r['holdout_recall_group_bootstrap95_high'])}) | "
                     f"{pct(r['holdout_precision_missing_low'])}-{pct(r['holdout_precision_missing_high'])} / "
                     f"{pct(r['holdout_recall_missing_low'])}-{pct(r['holdout_recall_missing_high'])} | "
                     f"${r['holdout_selected_input_usd']:.4f} + ${r['holdout_selected_output_usd']:.4f} = ${r['holdout_selected_total_usd']:.4f} |")
    lines += ["", "## Transfer from selection", "",
              "![Selection and holdout precision/recall](precision_recall.png)", "",
              "Bars compare the same fixed score cutoffs. Labels show KEEP counts/denominators; whiskers are percentile intervals from 1,000 website-group bootstrap resamples within each split.", "",
              "![Holdout composition and calculated usage cost](composition_cost.png)", "",
              "UNKNOWN is counted as non-KEEP for precision; invalid responses remain unresolved. Reasoning tokens are already included in billed output tokens and are not charged twice.",
              "", "## Interpretation and limits", "",
              "- These are descriptive results for the 216-company holdout, not population recall. The group bootstrap reflects sampling variability conditional on this labelled set and group structure, not sector-sampling, missing-label or LLM-reference uncertainty. The Wilson precision bounds are in `policy_results.csv` as a separate iid descriptive calculation.",
              "- Missing-label bounds assign unresolved cases extreme KEEP/non-KEEP outcomes; they are not confidence intervals. Small KEEP denominators can make these intervals wide. Empty denominators would be reported as n/a.",
              "- Costs are calculated from saved per-company GPT-6 Luna Batch token usage at documented rates; output includes reasoning. These are holdout-sample costs, not the conditional wider-population projections or guaranteed spending under the $100/$150/$200 scenario labels. The 20% reserve applies to prospective budgets only.",
              "- The user-reported $1.91 charge across all five original Batches remains unreconciled with calculated $1.944 selection-only usage. No invoice-confirmed holdout charge is available.",
              "- AI-profile outputs are LLM-derived labels, not independently verified ground truth. Evidence coverage, shared websites and unresolved labels restrict interpretation. This report does not optimize holdout cutoffs or choose a winner after seeing results.",
              "", "Artifacts: `policy_freeze.json`, `policy_results.csv`, `holdout_company_metrics.csv`, `recovery_audit.json`, `evaluation_manifest.json` and the two figures."]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--reconcile-selection-cost", action="store_true", help="Preserve the initial evaluation and write corrected selection-cost comparison")
    args = parser.parse_args()
    if args.reconcile_selection_cost:
        reconcile_selection_cost(args.output)
        print(args.output / "report_v2.md")
    else:
        result = run(args.output)
        print(json.dumps({k: v for k, v in result.items() if k != "sources"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
