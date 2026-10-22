"""Selection-only API usage costs and conditional cutoff-budget scenarios."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ai_ecosystem.calibration.analyze_validation_selection import (
    LABELS, MODELS, COLORS, SCORES, RUN, read_csv, sha256, summarize,
)


BASE = Path("data/interim/calibration/company_score_analysis/independent_validation_selection_v3")
POOL = Path("data/interim/calibration/acquisition_pools/independent_validation_v1")
OUTPUT = Path("data/interim/calibration/company_score_analysis/independent_validation_usage_costs_v2")
USER_REPORTED_FULL_BATCH_USD = 1.91
ELIGIBLE_COMPANIES = 165722
OBSERVED_POOL_PROCESSABLE = 1084 / 1500
PROCESSABILITY_SENSITIVITY = (0.60, 0.85)
COST_MULTIPLIER_SENSITIVITY = (0.80, 1.25)
CANDIDATES = {MODELS[0]: (0.0, 2.0, 4.0), MODELS[1]: (0.0, 1.0, 2.0)}


def selection_usage(path: Path) -> tuple[dict | None, str | None]:
    """Extract only trailing usage/model metadata, never decode profile text."""
    content = path.read_text(encoding="utf-8")
    position = content.rfind('"usage":')
    model_match = re.search(r'"returned_model":"([^"\\]+)"', content)
    usage = json.JSONDecoder().raw_decode(content[position + len('"usage":'):].lstrip())[0] if position >= 0 else None
    return usage, model_match.group(1) if model_match else None


def usage_cost(usage: dict, *, write_multiplier: float = 1.25) -> dict:
    incoming = usage["input_tokens"]
    detail = usage.get("input_tokens_details") or {}
    cached = detail.get("cached_tokens", 0)
    written = detail.get("cache_write_tokens", 0)
    ordinary = incoming - cached - written
    output = usage["output_tokens"]
    reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
    if min(incoming, cached, written, ordinary, output, reasoning) < 0 or reasoning > output:
        raise ValueError("Invalid token usage breakdown")
    if usage.get("total_tokens") is not None and usage["total_tokens"] != incoming + output:
        raise ValueError("Input/output total-token arithmetic mismatch")
    if detail.get("cache_write_tokens_details"):
        lifetime = detail["cache_write_tokens_details"]
        if lifetime.get("ttl_12h_tokens", 0):
            raise ValueError("12-hour cache writes require separate pricing")
        if lifetime.get("ttl_30m_tokens", 0) != written:
            raise ValueError("Cache-write lifetime breakdown mismatch")
    long = incoming > 272000
    input_factor = 2 if long else 1
    output_factor = 1.5 if long else 1
    ordinary_cost = ordinary * 0.05 * input_factor / 1_000_000
    cached_cost = cached * 0.005 * input_factor / 1_000_000
    write_cost = written * 0.05 * write_multiplier * input_factor / 1_000_000
    output_cost = output * 0.25 * output_factor / 1_000_000
    return {"input_tokens": incoming, "ordinary_input_tokens": ordinary, "cached_input_tokens": cached,
            "cache_write_tokens": written, "output_tokens": output, "reasoning_output_tokens_included": reasoning,
            "nonreasoning_output_tokens": output - reasoning, "long_context_pricing": long,
            "ordinary_input_usd": ordinary_cost, "cached_input_usd": cached_cost,
            "cache_write_input_usd": write_cost, "input_cost_usd": ordinary_cost + cached_cost + write_cost,
            "output_cost_usd": output_cost,
            "total_cost_usd": ordinary_cost + cached_cost + write_cost + output_cost}


def load_rows() -> tuple[list[dict], dict]:
    baseline = read_csv(BASE / "company_analysis.csv")
    if len(baseline) != 865 or len({r["company_id"] for r in baseline}) != 865:
        raise ValueError("5.5b selection table has duplicate or missing company IDs")
    provenance = json.loads((BASE / "analysis_manifest.json").read_text())
    if (provenance["score_source_sha256"] != sha256(SCORES) or
            provenance["selection_manifest_sha256"] != sha256(RUN / "company_manifest.jsonl")):
        raise ValueError("5.5b source identity changed")
    if Counter(r["analysis_label"] for r in baseline) != {
            "KEEP": 52, "UNKNOWN": 60, "EXCLUDE": 734, "UNRESOLVED": 19}:
        raise ValueError("5.5b selection labels changed")
    result_paths = sorted((RUN / "selection_results").glob("*.json"))
    if len(result_paths) != 865 or {p.stem for p in result_paths} != {r["company_id"] for r in baseline}:
        raise ValueError("Selection usage/result membership mismatch")
    records = {r["company_id"]: r for r in baseline}
    rows = []
    missing_usage = []
    for path in result_paths:
        row = records[path.stem]
        usage, model = selection_usage(path)
        if not usage:
            missing_usage.append(path.stem)
            continue
        if model != "gpt-6-luna":
            raise ValueError(f"Unexpected response model for {path.stem}: {model}")
        cost = usage_cost(usage)
        flat_write = usage_cost(usage, write_multiplier=1.0)
        rows.append({"company_id": path.stem, "analysis_label": row["analysis_label"],
                     "label_source": row["label_source"], "group_id": row["group_id"],
                     "website_id": row["website_id"], "sector": row["sector"], "model": model,
                     MODELS[0]: float(row[MODELS[0]]), MODELS[1]: float(row[MODELS[1]]),
                     **cost, "flat_write_sensitivity_total_usd": flat_write["total_cost_usd"],
                     "source_result_sha256": sha256(path)})
    rows.sort(key=lambda r: r["company_id"])
    return rows, {"missing_usage_company_ids": missing_usage, "selection_count": 865,
                  "usage_count": len(rows), "label_counts": dict(Counter(r["analysis_label"] for r in rows))}


def quantile(values: list[float], q: float) -> float | None:
    return float(np.quantile(values, q)) if values else None


def label_summary(rows: list[dict]) -> list[dict]:
    result = []
    for label in LABELS:
        group = [r for r in rows if r["analysis_label"] == label]
        inputs = [r["input_tokens"] for r in group]
        outputs = [r["output_tokens"] for r in group]
        result.append({"label": label, "companies": len(group),
                       "input_mean": float(np.mean(inputs)), "input_median": quantile(inputs, .5),
                       "input_p90": quantile(inputs, .9), "input_p95": quantile(inputs, .95),
                       "output_mean": float(np.mean(outputs)), "output_median": quantile(outputs, .5),
                       "output_p90": quantile(outputs, .9), "output_p95": quantile(outputs, .95),
                       "mean_input_usd": float(np.mean([r["input_cost_usd"] for r in group])),
                       "total_input_usd": sum(r["input_cost_usd"] for r in group),
                       "mean_output_usd": float(np.mean([r["output_cost_usd"] for r in group])),
                       "total_output_usd": sum(r["output_cost_usd"] for r in group),
                       "total_usage_cost_usd": sum(r["total_cost_usd"] for r in group),
                       "total_cache_write_tokens": sum(r["cache_write_tokens"] for r in group),
                       "total_cached_input_tokens": sum(r["cached_input_tokens"] for r in group),
                       "total_reasoning_output_tokens_included": sum(r["reasoning_output_tokens_included"] for r in group)})
    return result


def cutoff_metrics(selected: list[dict]) -> dict:
    proxy = [{**r, "estimated_batch_input_usd": r["input_cost_usd"],
              "estimated_batch_output_reserved_usd": r["output_cost_usd"],
              "estimated_batch_total_reserved_usd": r["total_cost_usd"]} for r in selected]
    metrics = summarize(proxy)
    metrics["usage_input_usd"] = metrics.pop("estimated_batch_input_usd")
    metrics["usage_output_usd"] = metrics.pop("estimated_batch_output_reserved_usd")
    metrics["usage_total_usd"] = metrics.pop("estimated_batch_total_reserved_usd")
    metrics["flat_write_sensitivity_usd"] = sum(r["flat_write_sensitivity_total_usd"] for r in selected)
    return metrics


def curves(rows: list[dict]) -> list[dict]:
    output = []
    for model in MODELS:
        ordered = sorted(rows, key=lambda r: (-r[model], r["company_id"]))
        cutoffs = sorted({r[model] for r in rows} | set(CANDIDATES[model]), reverse=True)
        cutoffs.insert(0, math.nextafter(cutoffs[0], math.inf))
        index = 0
        for cutoff in cutoffs:
            while index < len(ordered) and ordered[index][model] >= cutoff:
                index += 1
            output.append({"model": model, "cutoff": cutoff, "comparison": "score >= cutoff, ties included",
                           "historical_candidate": cutoff in CANDIDATES[model],
                           **cutoff_metrics(ordered[:index])})
    return output


def scenarios(curves_rows: list[dict]) -> list[dict]:
    output = []
    q = OBSERVED_POOL_PROCESSABLE
    for row in curves_rows:
        selected_fraction = row["selected_total"] / 865
        processable_count = ELIGIBLE_COMPANIES * q
        ratio = processable_count / 865
        projected_cost = row["usage_total_usd"] * ratio
        low_cost = row["flat_write_sensitivity_usd"] / 865 * ELIGIBLE_COMPANIES * PROCESSABILITY_SENSITIVITY[0] * COST_MULTIPLIER_SENSITIVITY[0]
        high_cost = row["usage_total_usd"] / 865 * ELIGIBLE_COMPANIES * PROCESSABILITY_SENSITIVITY[1] * COST_MULTIPLIER_SENSITIVITY[1]
        output.append({**row, "scenario_eligible_frame": ELIGIBLE_COMPANIES,
                       "scenario_processability": q, "scenario_processable_count": processable_count,
                       "scenario_selected_fraction_given_processable": selected_fraction,
                       "scenario_selected_companies": processable_count * selected_fraction,
                       "scenario_usage_cost_usd": projected_cost,
                       "scenario_cost_low_usd": low_cost, "scenario_cost_high_usd": high_cost})
    return output


def budget_options(scenario_rows: list[dict]) -> list[dict]:
    result = []
    for total_budget in (50, 100):
        planned = total_budget * .8
        for model in MODELS:
            choices = [r for r in scenario_rows if r["model"] == model and
                       r["selected_keep"] >= 3 and r["scenario_usage_cost_usd"] <= planned]
            for target_share in (.50, .70, .95):
                target = planned * target_share
                chosen = min(choices, key=lambda r: (abs(r["scenario_usage_cost_usd"] - target),
                                                      -r["selected_keep"]))
                result.append({"total_budget_usd": total_budget, "spending_reserve_usd": total_budget * .2,
                               "planned_requests_usd": planned, "target_budget_share": target_share,
                               "near_boundary_uncertain": chosen["scenario_cost_high_usd"] > planned,
                               **chosen})
    return result


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot_cost_components(summary: list[dict], rows: list[dict], output: Path) -> None:
    x = np.arange(len(summary))
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].bar(x, [r["mean_input_usd"] for r in summary], color="#2f7185", label="Input")
    axes[0].bar(x, [r["mean_output_usd"] for r in summary], bottom=[r["mean_input_usd"] for r in summary],
                color="#d78b37", label="Output incl. reasoning")
    axes[0].set_xticks(x, LABELS, rotation=20)
    axes[0].set_ylabel("Calculated USD per company")
    axes[0].set_title("Mean usage-based cost by label")
    axes[0].legend()
    axes[1].boxplot([[r["output_tokens"] for r in rows if r["analysis_label"] == label]
                     for label in LABELS], tick_labels=LABELS, showfliers=False)
    axes[1].set_ylabel("Billed output tokens, including reasoning")
    axes[1].set_title("Output-token distribution by label")
    for ax in axes:
        ax.grid(axis="y", alpha=.2)
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)


def plot_cutoffs(rows: list[dict], options: list[dict], model: str, output: Path) -> None:
    group = sorted((r for r in rows if r["model"] == model), key=lambda r: r["cutoff"])
    x = [r["usage_total_usd"] for r in group]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    axes[0].plot(x, [r["observed_keep_recall"] for r in group], color=COLORS["KEEP"])
    axes[0].set_xlabel("Calculated selection-set USD")
    axes[0].set_ylabel("Observed KEEP recall (of 52)")
    axes[0].set_title("Cost versus observed KEEP capture")
    axes[1].plot([r["selected_total"] for r in group], x, color="#2f7185")
    axes[1].set_xlabel("Companies selected")
    axes[1].set_ylabel("Cumulative calculated USD")
    axes[1].set_title("Cumulative cost as shortlist expands")
    for ax in axes:
        ax.grid(alpha=.2)
    for point in group:
        if point["historical_candidate"]:
            axes[0].scatter(point["usage_total_usd"], point["observed_keep_recall"], color="#a4314b", zorder=3)
            axes[0].annotate(f">={point['cutoff']:g}", (point["usage_total_usd"], point["observed_keep_recall"]),
                             xytext=(5, 5), textcoords="offset points", fontsize=8)
    fig.suptitle(f"{model}: selection-set usage costs")
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def markdown(summary: list[dict], rows: list[dict], scenario: list[dict], options: list[dict], audit: dict) -> str:
    calculated = sum(r["total_cost_usd"] for r in rows)
    alternative = sum(r["flat_write_sensitivity_total_usd"] for r in rows)
    lines = ["# Selection-set actual-usage cost and budget scenarios", "",
             "Only the 865 selection companies and saved usage metadata were read; response bodies and holdout labels were not inspected. The 5.5b label view is unchanged: 52 KEEP, 60 UNKNOWN, 734 EXCLUDE and 19 unresolved (three labels are separately recovered 5.5a results).",
             "", "## Billing basis", "",
             f"- **User-reported charge:** ${USER_REPORTED_FULL_BATCH_USD:.2f} for all five original Batches and both splits. It is an aggregate billing report, not a selection-only amount; no per-company share was imputed from it.",
             f"- **Calculated selection-only usage cost:** ${calculated:.3f} under documented Luna Batch rates; ${alternative:.3f} if cache writes were charged at the ordinary input rate instead of the documented 1.25x rate. Neither is a confirmed charge. The documented-rate selection estimate exceeds the user-reported full-run charge, an unresolved reconciliation issue; account invoice/credits, timing and actual processing terms need checking before treating it as billing truth.",
             "- Pricing: GPT-6 Luna Batch short-context rates per million tokens are $0.05 ordinary input, $0.005 cached read, $0.0625 default 30-minute cache write and $0.25 output. Input >272k would double input/cache rates and multiply output by 1.5 for the entire request; no saved selection response crosses that threshold. Requests contain no 12-hour cache setting, and saved usage has no TTL breakdown. Output usage already includes reasoning tokens, so reasoning was not charged again.",
             "- Sources checked 2026-10-21: [official OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna), [prompt-caching billing rules](https://developers.openai.com/api/docs/guides/prompt-caching), and [Batch discount](https://developers.openai.com/api/docs/guides/batch). No API or billing-account query was made.",
             f"- Missing usage: {len(audit['missing_usage_company_ids'])} of 865. Input arithmetic and model identity were checked per company.",
             "", "## By label", "",
             "| Label | Companies | Input mean / median / p90 / p95 | Output mean / median / p90 / p95 | Mean input / output USD | Total input / output USD |",
             "|---|---:|---:|---:|---:|---:|"]
    for r in summary:
        lines.append(f"| {r['label']} | {r['companies']} | "
                     f"{r['input_mean']:,.0f} / {r['input_median']:,.0f} / {r['input_p90']:,.0f} / {r['input_p95']:,.0f} | "
                     f"{r['output_mean']:,.0f} / {r['output_median']:,.0f} / {r['output_p90']:,.0f} / {r['output_p95']:,.0f} | "
                     f"${r['mean_input_usd']:.4f} / ${r['mean_output_usd']:.4f} | "
                     f"${r['total_input_usd']:.3f} / ${r['total_output_usd']:.3f} |")
    lines.extend(["", "![Mean input/output cost and output-token distributions](cost_components.png)", "",
                  "KEEP responses have higher mean input and output tokens than EXCLUDE responses in this selection set; mean calculated KEEP cost is about 3.3x EXCLUDE cost. Input dominates total calculated cost (about 94.4% overall). These are associations with evidence length and response detail, not a causal effect of the label. Per-company token and cost components are in `company_usage_costs.csv`.",
                  "", "## Cutoff comparisons", "",
                  "Each model's table contains every distinct observed score cutoff, the historical candidates, and a zero-selection row. Selection uses `score >= cutoff` and retains all ties. Unresolved companies contribute to selected counts and cost, not EXCLUDE labels. Confirmed KEEP precision includes UNKNOWN in its labelled denominator; observed KEEP recall uses all 52 labelled KEEPs. Missing-label bounds are extreme assignments, not confidence intervals."])
    for model in MODELS:
        lines.extend(["", f"### {model}", "", f"![Cost and recall curves]({model}_cost_curves.png)", "",
                      "Annotated points are the prior candidate cutoffs. The left plot shows how much observed KEEP recall each additional selection-set dollar buys; the right plot shows cumulative cost."])
    lines.extend(["", "## Wider eligible-frame scenario", "",
                  "The saved frame contains 165,722 companies with eligible normalized domains after recorded development/domain exclusions. The pool of 1,500 used an 80% sector-proportional plus 20% equal-sector design with sector-specific pool probabilities. Only 1,084/1,500 were PROCESSABLE before overlap/package exclusions. The later 865/216 selection/holdout split was balanced by website/content group and sector, not an independent known-probability draw; evidence failures and acquisition gaps may vary by sector. Therefore **full-pool inverse-probability weights cannot be applied to the 865 selection cases**, and no population-weighted estimate or AI recall is claimed.",
                  f"Conditional scenario only: among the 165,722 eligible-frame firms, assume {OBSERVED_POOL_PROCESSABLE:.1%} have usable evidence and that these processable firms have the same joint score/per-company-cost distribution as the 865 selection companies. Then projected processable count is {ELIGIBLE_COMPANIES * OBSERVED_POOL_PROCESSABLE:,.0f}; a cutoff's selected fraction and cost per processable firm are multiplied by that count. This is **not** an estimate for all Austrian companies. Sensitivity uses processability 60%-85%, per-company cost 0.8x-1.25x, and ordinary-rate cache writes at the low end. Shared websites may correlate scores and costs, and legal-company LLM requests may still be duplicated unless a future protocol explicitly reuses them.",
                  "", "## New first-pass budgets", "",
                  "The $50 and $100 totals are new first-pass budgets, **not** remaining validation money. A 20% reserve leaves $40 or $80 for planned requests. The model/cutoff is not frozen. Counts and dollars below are conditional scenarios; observed precision/recall and missing-label bounds remain selection-set quantities.",
                  "", "| Budget (plan + reserve) | Model | Cutoff >= | Observed selected / KEEP | Projected selected | Projected cost (sensitivity) | KEEP precision / observed recall | Selected unresolved | Precision bounds / recall bounds | Boundary |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"])
    for r in options:
        lines.append(f"| ${r['total_budget_usd']} (${r['planned_requests_usd']:.0f} + ${r['spending_reserve_usd']:.0f}) | `{r['model']}` | {r['cutoff']:.3f} | "
                     f"{r['selected_total']} / {r['selected_keep']} | {r['scenario_selected_companies']:,.0f} | "
                     f"${r['scenario_usage_cost_usd']:.1f} (${r['scenario_cost_low_usd']:.1f}-${r['scenario_cost_high_usd']:.1f}) | "
                     f"{pct(r['keep_precision_labelled'])} / {pct(r['observed_keep_recall'])} | {r['selected_unresolved']} | "
                     f"{pct(r['precision_missing_low'])}-{pct(r['precision_missing_high'])} / "
                     f"{pct(r['recall_missing_low'])}-{pct(r['recall_missing_high'])} | "
                     f"{'uncertain' if r['near_boundary_uncertain'] else 'within sensitivity range'} |")
    lines.extend(["", "These options are illustrative cost points from observed thresholds, not recommendations to deploy. The sensitivity ranges are assumptions, not statistical intervals; options whose upper cost exceeds the planned amount are marked uncertain. Selection-set KEEPs at the stricter cutoffs are few, so their observed precision is unstable. Neither $50 nor $100 scenario reaches convincing high-precision and high-recall selection.",
                  "For scale, cutoff 0 would cost about $120 for the reference (76.9% observed KEEP recall) or $127 for the challenger (92.3%), before the 20% reserve, under the same conditional processability/score/cost assumptions. On the challenger's saved selection curve, expanding beyond cutoff 0 approximately doubles sample usage cost to recover the last 7.7 percentage points of observed KEEP recall; this is diminishing return, not population recall evidence.",
                  "", "## Limits", "",
                  "The $1.91 billing report and per-company calculated costs remain unreconciled. The latter are suitable for comparative cutoff accounting, with the pricing assumption stated, but not invoice reconciliation. The historical prompt/output pattern may differ from a future final-pass contract; website coverage, score distribution, prompt caching, rates and shared-site handling could change. No population AI prevalence or recall is inferred. Holdout remains unopened.",
                  "", "Artifacts: `company_usage_costs.csv`, `label_cost_summary.csv`, `cutoff_usage_curves.csv`, `conditional_population_scenarios.csv`, `budget_options.csv`, `analysis_manifest.json`.", ""])
    return "\n".join(lines)


def build(output: Path = OUTPUT) -> dict:
    if output.exists():
        raise FileExistsError(output)
    rows, audit = load_rows()
    if audit["missing_usage_company_ids"]:
        raise ValueError("Missing selection usage prevents complete cost curves")
    frame = json.loads((POOL / "frame.json").read_text())
    pool = read_csv(POOL / "revisions/v000001/pool.csv")
    if (frame["eligible_count"] != ELIGIBLE_COMPANIES or len(pool) != 1500 or
            any(not r["pool_inclusion_probability"] for r in pool)):
        raise ValueError("Saved pool probability/eligible-frame contract changed")
    summary = label_summary(rows)
    curve_rows = curves(rows)
    scenario_rows = scenarios(curve_rows)
    options = budget_options(scenario_rows)
    output.mkdir(parents=True)
    write_csv(output / "company_usage_costs.csv", rows)
    write_csv(output / "label_cost_summary.csv", summary)
    write_csv(output / "cutoff_usage_curves.csv", curve_rows)
    write_csv(output / "conditional_population_scenarios.csv", scenario_rows)
    write_csv(output / "budget_options.csv", options)
    plot_cost_components(summary, rows, output / "cost_components.png")
    for model in MODELS:
        plot_cutoffs(curve_rows, options, model, output / f"{model}_cost_curves.png")
    (output / "report.md").write_text(markdown(summary, rows, scenario_rows, options, audit), encoding="utf-8")
    manifest = {"analysis_version": "independent_validation_usage_costs_v2", "selection_count": len(rows),
                "label_counts": audit["label_counts"], "missing_usage_company_ids": audit["missing_usage_company_ids"],
                "user_reported_full_five_batch_usd": USER_REPORTED_FULL_BATCH_USD,
                "calculated_selection_usage_usd": sum(r["total_cost_usd"] for r in rows),
                "flat_write_sensitivity_selection_usd": sum(r["flat_write_sensitivity_total_usd"] for r in rows),
                "pricing_checked_date": "2026-10-21", "pricing_sources": [
                    "https://developers.openai.com/api/docs/pricing",
                    "https://developers.openai.com/api/docs/models/gpt-6-luna",
                    "https://developers.openai.com/api/docs/guides/prompt-caching",
                    "https://developers.openai.com/api/docs/guides/batch"],
                "pool_frame_sha256": sha256(POOL / "frame.json"),
                "pool_csv_sha256": sha256(POOL / "revisions/v000001/pool.csv"),
                "selection_table_sha256": sha256(BASE / "company_analysis.csv"),
                "population_projection_status": "conditional_scenario_not_weighted_estimate",
                "eligible_frame_companies": ELIGIBLE_COMPANIES,
                "processable_scenario_rate": OBSERVED_POOL_PROCESSABLE}
    (output / "analysis_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))


if __name__ == "__main__":
    main()
