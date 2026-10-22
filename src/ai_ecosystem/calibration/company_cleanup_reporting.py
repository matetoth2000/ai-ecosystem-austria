"""Predeclared cleanup evaluation, diagnostics and freeze decisions."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .company_cleanup_features import CANDIDATES
from .company_modeling_common import file_hash
from .company_tuning_contract import valid_checkpoint, write_json, write_table
from .company_tuning_metrics import aggregate_outer, paired_uncertainty, select_inner
from .run_company_model_tuning import diagnostics


def choose_candidate(table, tolerance):
    near = table[table.average_precision >= table.average_precision.max()-tolerance].copy()
    near['complexity'] = near.candidate.map(lambda c: len(CANDIDATES[c]))
    return near.sort_values(['operational_f1', 'selected_share', 'complexity', 'candidate'],
                           ascending=[False, True, True, True]).iloc[0].candidate


def freeze_decision(summary, repetitions, intervals, config):
    table = summary[~summary.weighted].set_index('candidate')
    ref = table.loc['reference_logistic_size']; g = config['improvement']; decisions = []
    for candidate in CANDIDATES:
        if candidate == 'reference_logistic_size': continue
        row = table.loc[candidate]
        delta = row[['average_precision', 'operational_f1', 'selected_share', 'keep_recall']].astype(float)-ref[['average_precision', 'operational_f1', 'selected_share', 'keep_recall']].astype(float)
        ci = intervals[intervals.candidate.eq(candidate)].set_index('metric')
        reps = repetitions[~repetitions.weighted & repetitions.candidate.eq(candidate)].set_index('repetition')
        refs = repetitions[~repetitions.weighted & repetitions.candidate.eq('reference_logistic_size')].set_index('repetition')
        ap = (delta.average_precision >= g['ap'] and ci.loc['average_precision', 'paired_delta_lower'] > 0
              and delta.operational_f1 >= -g['f1_loss'] and (reps.average_precision >= refs.average_precision).all())
        operational = (abs(delta.average_precision) <= config['ap_tolerance'] and delta.operational_f1 >= g['f1']
            and delta.selected_share <= -g['effort'] and delta.keep_recall >= -g['recall_loss']
            and ci.loc['operational_f1', 'paired_delta_lower'] > 0
            and (reps.operational_f1 > refs.operational_f1).all() and (reps.selected_share < refs.selected_share).all())
        decisions.append({'candidate': candidate, **{k+'_delta': float(v) for k, v in delta.items()},
                          'ap_route': bool(ap), 'operational_route': bool(operational), 'passed': bool(ap or operational)})
    passing = [r['candidate'] for r in decisions if r['passed']]
    choices = table.reset_index()
    choices = choices[choices.candidate.isin(passing or [c for c in CANDIDATES if c != 'reference_logistic_size'])]
    challenger = choose_candidate(choices, config['ap_tolerance'])
    return {'primary': challenger if passing else 'reference_logistic_size',
            'challenger': challenger, 'frozen_candidates': ['reference_logistic_size', challenger],
            'gates': decisions, 'status': 'frozen_development_models_awaiting_independent_validation',
            'production_threshold': None}


def final_settings(root, config):
    predictions = []
    for path in sorted((Path(root)/'fits').glob('*/complete.json')):
        marker = valid_checkpoint(path.parent); part = marker['partition']
        if part['inner_fold'] is None: continue
        unique_fold = ((part['repetition']*config['outer_folds']+part['outer_fold'])*config['inner_folds']+part['inner_fold'])
        predictions.append(pd.read_parquet(path.parent/'predictions.parquet').assign(inner_fold=unique_fold))
    table, choices, _ = select_inner(pd.concat(predictions, ignore_index=True), config['ap_tolerance'])
    return table, {r['candidate']: float(r['C']) for r in choices}


def report(root, predictions, chosen, numeric, config, progress):
    root = Path(root); directory = root/'reports'
    if valid_checkpoint(directory): return json.loads((directory/'freeze_decision.json').read_text())
    directory.mkdir(parents=True, exist_ok=True)
    metrics, curves, reps, summary = aggregate_outer(predictions)
    for name, frame in [('outer_metrics', metrics), ('operating_points', curves),
                        ('repetition_metrics', reps), ('candidate_metrics', summary)]:
        write_table(directory/(name+'.parquet'), frame)
    progress.update(current_stage='paired_group_bootstrap', candidate=None)
    intervals = paired_uncertainty(predictions, config)
    write_table(directory/'paired_intervals.parquet', intervals)
    chosen_metrics, _, _, _ = aggregate_outer(chosen.assign(candidate='inner_selected_procedure'))
    write_table(directory/'selection_procedure_metrics.parquet', chosen_metrics)
    diagnostics(directory, predictions, numeric, config, candidates=CANDIDATES)
    terms = []; blocks = []; vocab = []
    for path in sorted((root/'fits').glob('*/complete.json')):
        saved = valid_checkpoint(path.parent); part = saved['partition']
        if part['inner_fold'] is not None: continue
        coef = pd.read_parquet(path.parent/'coefficients.parquet')
        for _, group in coef.groupby('block'):
            for row in group.nlargest(30, 'mean_absolute_feature_contribution').to_dict('records'):
                terms.append({**row, 'candidate': saved['candidate'], 'repetition': part['repetition'], 'outer_fold': part['outer_fold']})
        for row in json.loads((path.parent/'block_contributions.json').read_text()):
            blocks.append({**row, 'candidate': saved['candidate'], 'repetition': part['repetition'], 'outer_fold': part['outer_fold']})
    write_table(directory/'coefficient_diagnostics.parquet', pd.DataFrame(terms))
    write_table(directory/'block_contributions.parquet', pd.DataFrame(blocks))
    for path in sorted((root/'preprocessing').glob('*/grounded_diagnostic.json')):
        current = json.loads(path.read_text()); marker = json.loads((path.parent/'complete.json').read_text())
        parent = marker['parent']
        previous = json.loads((Path(parent['directory'])/'grounded_diagnostic.json').read_text()) if parent else {}
        vocab.append({'partition': path.parent.name, 'previous_channel_features': previous.get('all_size'), **current})
    write_json(directory/'grounded_vocabulary_comparison.json', vocab)
    decision = freeze_decision(summary, reps, intervals, config)
    write_json(directory/'freeze_decision.json', decision)
    setting_table, settings = final_settings(root, config)
    write_table(directory/'final_inner_settings.parquet', setting_table)
    write_json(directory/'final_regularization.json', settings)
    lines = ['# Company AI-Relevance Cleanup v3', '',
        'Repeated nested grouped development CV; not independent validation. No production threshold.', '',
        'KEEP is desired; UNKNOWN unresolved but costly; EXCLUDE negative under historical calibration. UNKNOWN is never negative supervision. NO_EVIDENCE is unscored.', '',
        '| Candidate | AP | Operational F1 | KEEP recall | Selected share | KEEP / UNKNOWN / EXCLUDE | Effort 80 / 90 / 95 |',
        '|---|---:|---:|---:|---:|---|---|']
    for r in summary[~summary.weighted].sort_values('candidate').itertuples():
        lines.append(f'| {r.candidate} | {r.average_precision:.4f} | {r.operational_f1:.4f} | {r.keep_recall:.4f} | {r.selected_share:.4f} | {r.selected_keep:.1f} / {r.selected_unknown:.1f} / {r.selected_exclude:.1f} | {r.effort_80:.3f} / {r.effort_90:.3f} / {r.effort_95:.3f} |')
    lines += ['', f"Frozen primary: **{decision['primary']}**. Cleaned comparator: **{decision['challenger']}**.",
        'The reference and selected cleaned comparator are both retained for future independent validation. Final C uses saved inner CV only; full-data fit performance is not an evaluation.', '',
        f"Grounded features before cleanup: {min(v['previous_channel_features'] for v in vocab)}-{max(v['previous_channel_features'] for v in vocab)} channel columns; after filtering: {min(v['all_size'] for v in vocab)}-{max(v['all_size'] for v in vocab)} canonical phrases; after constant/vector cleanup: {min(v['after_vector_dedup_count'] for v in vocab)}-{max(v['after_vector_dedup_count'] for v in vocab)} columns.",
        f"UNKNOWN changes at least 20% of the pre-vector-collapse vocabulary in {sum(v['meaningful_difference_flag'] for v in vocab)}/{len(vocab)} partitions. Not a separate fitted candidate.", '',
        'See per-partition grounded_terms.json for removed fragments and retained technical/conventional phrases, and alias provenance in grounded_diagnostic.json. Category provenance is not contextual attribution.',
        'Metric tables include standard and operational measures, group-weighted sensitivity, per-fold/repetition results and conditional paired uncertainty. Subgroup tables describe within-subgroup ranking, not global-selection composition.',
        'Historical visibility conflicts are copied under deterministic_features: strong matches on omitted pages remain diagnostics, not new labels or automatic error corrections.',
        'No further tuning is authorized. Next: independent validation using new companies and broader website evidence.', '']
    (directory/'comparison_report.md').write_text('\n'.join(lines))
    write_json(directory/'complete.json', {'files': {p.name: file_hash(p) for p in directory.iterdir() if p.is_file() and p.name != 'complete.json'}})
    return decision
