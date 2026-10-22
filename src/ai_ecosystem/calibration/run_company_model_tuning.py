"""Versioned nested full-development tuning. No source DB or API access."""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import resource
import shutil
import sys
import time
import warnings

import joblib
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from .company_experiment_metrics import group_weights
from .company_modeling_common import canonical, digest, file_hash
from .company_tuning_contract import (CANDIDATES, Progress, load_config, load_full_cohort,
    make_schedule, settings, valid_checkpoint, write_json, write_table)
from .company_tuning_features import (candidate_matrix, document_maps, expanded_lexical,
    prepare_features, saturation_features)
from .company_tuning_metrics import aggregate_outer, paired_uncertainty, select_inner


def implementation_files():
    base = Path(__file__).parent
    names = ['run_company_model_tuning.py', 'company_tuning_contract.py', 'company_tuning_features.py',
             'company_tuning_metrics.py', 'company_experiment_features.py', 'company_experiment_metrics.py',
             'company_modeling_common.py', 'company_modeling_groups.py', 'company_modeling_preprocessing.py',
             'attribution_features.py']
    return {name: base/name for name in names}


def fit_candidate(root, cache, pre_directory, partition, candidate, c, frame, config, progress,
                  candidates=None):
    candidates = CANDIDATES if candidates is None else candidates
    key = digest(canonical({'preprocessing': pre_directory.name, 'partition': partition,
                            'candidate': candidate, 'C': c}))
    directory = Path(root)/'fits'/key
    if valid_checkpoint(directory):
        progress.update(cache_status='fit_hit', candidate=candidate, setting=c,
                        last_successful_checkpoint=str(directory/'complete.json'))
        return pd.read_parquet(directory/'predictions.parquet'), directory
    progress.update(current_stage='fitting', cache_status='fit_miss', candidate=candidate, setting=c)
    directory.mkdir(parents=True, exist_ok=True)
    matrix = sparse.hstack([cache['blocks'][b] for b in candidates[candidate]], format='csr')
    positions = {cid: i for i, cid in enumerate(cache['ids'])}
    training = frame[frame.bvd_id.isin(partition['train_ids'])]
    supervised = training[training.state.eq('PROCESSABLE') & training.label.isin(['KEEP', 'EXCLUDE'])]
    if set(supervised.label) != {'KEEP', 'EXCLUDE'}: raise ValueError('Both training classes required')
    indexes = [positions[cid] for cid in supervised.bvd_id]
    weights = group_weights(supervised); weights /= weights.mean()
    model = LogisticRegression(C=c, solver='liblinear', max_iter=config['maximum_iterations'],
                               random_state=config['seeds'][partition['repetition']])
    start = time.monotonic()
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        model.fit(matrix[indexes], supervised.label.eq('KEEP').to_numpy(int), sample_weight=weights)
    evaluation = frame[frame.bvd_id.isin(partition['evaluation_ids'])].copy()
    evaluation['score'] = np.nan
    eligible = evaluation.state.eq('PROCESSABLE')
    evaluation.loc[eligible, 'score'] = model.decision_function(matrix[[positions[cid] for cid in evaluation.loc[eligible, 'bvd_id']]])
    evaluation = evaluation.assign(candidate=candidate, C=c, repetition=partition['repetition'],
                                   outer_fold=partition['outer_fold'], inner_fold=partition['inner_fold'],
                                   feature_dimensions=matrix.shape[1])
    write_table(directory/'predictions.parquet', evaluation)
    temporary = directory/'model.joblib.tmp'; joblib.dump(model, temporary, compress=3); temporary.replace(directory/'model.joblib')
    extra = []
    if partition['inner_fold'] is None:
        coefficients, contributions = [], []; offset = 0
        for block in candidates[candidate]:
            width = len(cache['names'][block]); coef = model.coef_[0, offset:offset+width]
            x = cache['blocks'][block][indexes]
            typical = np.asarray(abs(x).mean(axis=0)).ravel()*np.abs(coef) if width else np.array([])
            for name, value, importance in zip(cache['names'][block], coef, typical):
                coefficients.append({'block': block, 'feature': name, 'coefficient': float(value),
                                     'mean_absolute_feature_contribution': float(importance)})
            contributions.append({'block': block, 'mean_absolute_net_contribution': float(np.mean(np.abs(x @ coef))) if width else 0.,
                                  'interpretation': 'supervised_training_rows_not_causal_importance'})
            offset += width
        write_table(directory/'coefficients.parquet', pd.DataFrame(coefficients))
        write_json(directory/'block_contributions.json', contributions)
        extra = ['coefficients.parquet', 'block_contributions.json']
    files = ['predictions.parquet', 'model.joblib']+extra
    write_json(directory/'complete.json', {'candidate': candidate, 'C': c, 'partition': partition,
        'preprocessing': pre_directory.name, 'training_keep': int(supervised.label.eq('KEEP').sum()),
        'training_exclude': int(supervised.label.eq('EXCLUDE').sum()), 'training_unknown': 0,
        'training_no_evidence': 0, 'feature_dimensions': matrix.shape[1],
        'seconds': time.monotonic()-start,
        'files': {name: file_hash(directory/name) for name in files}})
    progress.update(completed_fits=progress.state['completed_fits']+1,
                    last_successful_checkpoint=str(directory/'complete.json'))
    return evaluation, directory


def diagnostics(root, predictions, numeric, config, candidates=None):
    candidates = CANDIDATES if candidates is None else candidates
    p = predictions.merge(numeric[['availability_unique_contents', 'relationship_shared',
                                   'language_de_proportion', 'language_en_proportion']],
                          left_on='bvd_id', right_index=True, validate='many_to_one')
    p['site_size'] = pd.cut(p.availability_unique_contents, [-1, 5, 20, 100, np.inf], labels=['1-5', '6-20', '21-100', '>100']).astype(str)
    p['language'] = np.where(p.language_de_proportion >= .5, 'de', np.where(p.language_en_proportion >= .5, 'en', 'mixed_missing'))
    p['rank_percentile'] = p.groupby(['candidate', 'repetition', 'outer_fold']).score.rank(method='average', pct=True)
    subgroup = []; examples = []; correlations = []
    from .company_tuning_metrics import at_fraction, selection_count
    for (candidate, rep, fold), group in p.groupby(['candidate', 'repetition', 'outer_fold']):
        q = float(group.selected_fraction.iloc[0])
        for field in ('site_size', 'language', 'calibration_source', 'relationship_shared'):
            for value, sub in group.groupby(field):
                row = at_fraction(sub, q)
                subgroup.append({'candidate': candidate, 'repetition': rep, 'outer_fold': fold,
                    'subgroup_field': field, 'subgroup': str(value), **row,
                    'small_sample_warning': row['eligible_count'] < 20 or row['eligible_keep'] < 5,
                    'interpretation': 'within_subgroup_ranking_not_global_selection_composition'})
        ordered = group[group.score.notna()].sort_values(['score', 'bvd_id'], ascending=[False, True])
        count = selection_count(len(ordered), round(q*100)); chosen = set(ordered.head(count).bvd_id)
        for kind, sub in [('low_keep', ordered[ordered.label.eq('KEEP')].tail(5)),
                          ('high_exclude', ordered[ordered.label.eq('EXCLUDE')].head(5)),
                          ('selected_unknown', ordered[ordered.label.eq('UNKNOWN') & ordered.bvd_id.isin(chosen)].head(5))]:
            for row in sub.to_dict('records'): examples.append({**row, 'example_type': kind})
        for (label, source), sub in group[group.score.notna()].groupby(['label', 'calibration_source']):
            correlation = sub.score.corr(sub.availability_unique_contents, method='spearman') if len(sub) >= 3 and sub.availability_unique_contents.nunique() > 1 else np.nan
            correlations.append({'candidate': candidate, 'repetition': rep, 'outer_fold': fold,
                'label': label, 'calibration_source': source, 'count': len(sub), 'size_score_spearman': correlation})
    write_table(root/'subgroups.parquet', pd.DataFrame(subgroup))
    write_table(root/'error_examples.parquet', pd.DataFrame(examples))
    write_table(root/'size_dependence.parquet', pd.DataFrame(correlations))
    wide = p.pivot(index=['bvd_id', 'repetition', 'outer_fold'], columns='candidate', values='rank_percentile').reset_index()
    for candidate in candidates:
        wide[candidate+'_rank_delta'] = wide[candidate]-wide.reference_logistic_size
    write_table(root/'disagreements.parquet', wide)


def report(root, predictions, frame, numeric, config, progress):
    progress.update(current_stage='reporting', candidate=None, cache_status=None)
    metrics, curves, repetition, summary = aggregate_outer(predictions)
    write_table(root/'outer_metrics.parquet', metrics); write_table(root/'operating_points.parquet', curves)
    write_table(root/'repetition_metrics.parquet', repetition); write_table(root/'candidate_metrics.parquet', summary)
    progress.update(current_stage='paired_group_bootstrap')
    intervals = paired_uncertainty(predictions, config)
    write_table(root/'paired_intervals.parquet', intervals)
    progress.update(current_stage='diagnostics')
    diagnostics(root, predictions, numeric, config)
    terms = []; blocks = []; runtime = []; vocabulary = []
    for marker in sorted((root/'fits').glob('*/complete.json')):
        saved = valid_checkpoint(marker.parent)
        runtime.append({k: v for k, v in saved.items() if k not in ('files', 'partition')} | {
            k: saved['partition'][k] for k in ('repetition', 'outer_fold', 'inner_fold')})
        if saved['partition']['inner_fold'] is not None: continue
        coefficients = pd.read_parquet(marker.parent/'coefficients.parquet')
        # Full coefficients stay in each fit; compact stability summaries cover
        # numeric features and the strongest observed text/phrase contributions.
        kept = coefficients[~coefficients.block.isin(['body_mean', 'titles_mean', 'body_top3', 'titles_top3', 'G2'])]
        # Select using names rather than groupby-apply's version-sensitive index.
        top = pd.concat([g.nlargest(30, 'mean_absolute_feature_contribution') for _, g in coefficients.groupby('block')])
        kept = pd.concat([kept, top]).drop_duplicates(['block', 'feature'])
        for row in kept.to_dict('records'):
            terms.append({**row, 'candidate': saved['candidate'], 'repetition': saved['partition']['repetition'], 'outer_fold': saved['partition']['outer_fold']})
        for row in json.loads((marker.parent/'block_contributions.json').read_text()):
            blocks.append({**row, 'candidate': saved['candidate'], 'repetition': saved['partition']['repetition'], 'outer_fold': saved['partition']['outer_fold']})
    coefficient_frame = pd.DataFrame(terms)
    write_table(root/'coefficient_diagnostics.parquet', coefficient_frame)
    stability = coefficient_frame.assign(positive=coefficient_frame.coefficient.gt(0).astype(int)).groupby(['candidate', 'block', 'feature'], as_index=False).agg(
        observed_fits=('coefficient', 'size'), positive_fraction=('positive', 'mean'), coefficient_mean=('coefficient', 'mean'), coefficient_std=('coefficient', 'std'),
        typical_contribution_mean=('mean_absolute_feature_contribution', 'mean'))
    write_table(root/'coefficient_stability.parquet', stability)
    write_table(root/'block_contributions.parquet', pd.DataFrame(blocks))
    write_table(root/'fit_runtime.parquet', pd.DataFrame(runtime))
    for path in sorted((root/'preprocessing').glob('*/grounded_diagnostic.json')):
        d = json.loads(path.read_text()); vocabulary.append({'partition': path.parent.name, **d})
    write_json(root/'grounded_vocabulary_comparison.json', vocabulary)
    result = summary[~summary.weighted].set_index('candidate'); ref = result.loc['reference_logistic_size']
    decisions = []
    for candidate in CANDIDATES:
        row = result.loc[candidate]
        fields = ['average_precision', 'operational_f1', 'selected_share', 'keep_recall']
        delta = row[fields].astype(float)-ref[fields].astype(float)
        interval = intervals[(intervals.candidate == candidate) & (intervals.metric == 'average_precision')].iloc[0]
        gate = config['improvement']
        ap_route = delta.average_precision >= gate['ap'] and interval.paired_delta_lower > 0 and delta.operational_f1 >= -gate['f1_loss']
        tradeoff_route = abs(delta.average_precision) <= config['ap_tolerance'] and delta.operational_f1 >= gate['f1'] and delta.selected_share <= -gate['effort'] and delta.keep_recall >= -gate['recall_loss']
        reps = repetition[(repetition.candidate == candidate) & ~repetition.weighted].set_index('repetition')
        refs = repetition[(repetition.candidate == 'reference_logistic_size') & ~repetition.weighted].set_index('repetition')
        direction = ((reps.average_precision >= refs.average_precision).all() if ap_route else
                     ((reps.operational_f1 >= refs.operational_f1) & (reps.selected_share <= refs.selected_share)).all())
        decisions.append({'candidate': candidate, 'ap_gain': float(delta.average_precision), 'f1_gain': float(delta.operational_f1),
            'selection_share_delta': float(delta.selected_share), 'recall_delta': float(delta.keep_recall),
            'numerical_gate_passed': bool((ap_route or tradeoff_route) and direction),
            'status': 'candidate_for_independent_validation_not_production' if (ap_route or tradeoff_route) and direction else 'reference_or_not_clearly_better'})
    write_json(root/'improvement_assessment.json', decisions)
    lines = ['# Company AI-Relevance Tuning v2', '',
        'All current companies, including the former holdout, are development evidence. Results are repeated nested grouped CV, not independent validation.', '',
        'Operational precision = selected KEEP / all selected. KEEP is desired; UNKNOWN is unresolved but costly; EXCLUDE is negative under current calibration. UNKNOWN is never negative supervision.', '',
        '| Candidate | AP | Operational F1 | KEEP recall | Selected share | KEEP / UNKNOWN / EXCLUDE (mean per outer fold) |',
        '|---|---:|---:|---:|---:|---|']
    for name, r in result.sort_values('average_precision', ascending=False).iterrows():
        lines.append(f'| {name} | {r.average_precision:.4f} | {r.operational_f1:.4f} | {r.keep_recall:.4f} | {r.selected_share:.4f} | {r.selected_keep:.1f} / {r.selected_unknown:.1f} / {r.selected_exclude:.1f} |')
    flagged = sum(d['meaningful_difference_flag'] for d in vocabulary)
    better = [d['candidate'] for d in decisions if d['numerical_gate_passed']]
    lines += ['', '## Interpretation', '',
        f"Numerical improvement gates: {', '.join(better) if better else 'no challenger clearly passes; retain the frozen reference'}. Subgroup diagnostics must accompany this assessment; no deployment model or threshold is created.",
        f'Grounded vocabulary: {flagged}/{len(vocabulary)} partitions flag at least {config["vocabulary_change_fraction"]:.0%} vocabulary change from UNKNOWN supervision. This is a diagnostic, not an additional model candidate.',
        'Compare candidate_metrics and paired_intervals; intervals are conditional on fitted models. Raw logits are never pooled across folds for headline metrics.',
        'operating_points contains all 100 fractions, standard precision/F1, operational precision/F1 and KEEP/UNKNOWN/EXCLUDE composition. Capture-effort 80/90/95% is diagnostic only.',
        'selection_procedure_predictions contains outer predictions from the candidate chosen only on inner folds. Candidate comparison still carries development-selection optimism.',
        'size_dependence, coefficient_stability, subgroups and error_examples expose shortcuts and small-sample limitations. Source/label gaps and unseen historical package evidence are not relabeled.',
        'Future independent evaluation must use new companies and broader evidence. Current operating fractions are not population deployment recommendations.', '']
    (root/'comparison_report.md').write_text('\n'.join(lines))


def run(config_path, resume=False, validate_only=False, stop_after_outer=None):
    config, lexicon = load_config(config_path)
    manifest, frame, numeric = load_full_cohort(config['dataset_directory'])
    folds, partitions, expected = make_schedule(frame, config)
    identity = {'config': config, 'lexicon': lexicon, 'dataset_hash': manifest['canonical_build_hash'],
                'environment': {'python': sys.version, **{name: version(name) for name in ('numpy', 'pandas', 'scipy', 'scikit-learn', 'pyarrow', 'joblib')}},
                'source_files': {name: file_hash(path) for name, path in implementation_files().items()},
                'dataset_files': {name: info['file_hash'] for name, info in manifest['tables'].items()},
                'candidates': CANDIDATES, 'evaluation_contract': 'all_current_data_development_v2'}
    # Canonical round-trip also converts tuples for identity comparisons on resume.
    identity = json.loads(canonical(identity))
    if validate_only:
        result = {'companies': len(frame), 'labels': frame.label.value_counts().to_dict(),
                  'states': frame.state.value_counts().to_dict(), 'expected_fits': expected,
                  'partitions': len(partitions), 'identity_hash': digest(canonical(identity))}
        print(json.dumps(result, indent=2)); return result
    root = Path(config['output_directory'])
    if root.exists():
        if not resume: raise FileExistsError(root)
        if not (root/'identity.json').exists() or json.loads((root/'identity.json').read_text()) != identity:
            raise ValueError('Resume identity mismatch; never overwrite an incompatible experiment')
    else:
        root.mkdir(parents=True); write_json(root/'identity.json', identity)
        (root/'source_snapshot').mkdir()
        for name, path in implementation_files().items(): shutil.copyfile(path, root/'source_snapshot'/name)
        write_json(root/'config_snapshot.json', config); write_json(root/'lexicon_snapshot.json', lexicon)
    if valid_checkpoint(root):
        print('Completed experiment verified; no refitting.'); return json.loads((root/'complete.json').read_text())
    progress = Progress(root, expected)
    try:
        write_table(root/'fold_assignments.parquet', folds); write_table(root/'cohort.parquet', frame)
        write_json(root/'schedule.json', {'partitions': partitions, 'expected_fits': expected})
        write_json(root/'manifest.json', {'experiment_id': config['experiment_id'],
            'identity_hash': digest(canonical(identity)), 'dataset_hash': manifest['canonical_build_hash'],
            'prediction_unit': 'bvd_id', 'evaluation': 'repeated_nested_full_development_cv',
            'expected_fits': expected, 'source_dataset_is_immutable': True,
            'learned_preprocessing': 'training_groups_only',
            'unknown_semantics': 'unresolved_but_costly_never_negative_supervision',
            'candidate_blocks': CANDIDATES})
        write_json(root/'cohort_audit.json', {'companies': len(frame), 'labels': frame.label.value_counts().to_dict(),
            'states': frame.state.value_counts().to_dict(), 'leakage_groups': frame.leakage_group_id.nunique(),
            'source_counts': frame.calibration_source.value_counts().to_dict(),
            'expected_counts_match': bool(len(frame) == 649 and frame.state.eq('PROCESSABLE').sum() == 476),
            'historical_splits_are_development_only': True})
        feature_root = root/'deterministic_features'
        if valid_checkpoint(feature_root):
            lexical = pd.read_parquet(feature_root/'lexical.parquet').set_index('bvd_id')
            saturated = pd.read_parquet(feature_root/'context.parquet').set_index('bvd_id')
        else:
            progress.update(current_stage='deterministic_features', cache_status='miss')
            lexical, evidence = expanded_lexical(config['dataset_directory'], frame, lexicon, config)
            saturated, anomalies = saturation_features(numeric, config['size_caps'])
            write_table(feature_root/'lexical.parquet', lexical.reset_index())
            write_table(feature_root/'context.parquet', saturated.reset_index())
            write_table(feature_root/'visibility_mismatches.parquet', evidence)
            write_json(feature_root/'source_count_anomalies.json', anomalies)
            files = ['lexical.parquet', 'context.parquet', 'visibility_mismatches.parquet', 'source_count_anomalies.json']
            write_json(feature_root/'complete.json', {'files': {name: file_hash(feature_root/name) for name in files}})
        documents = document_maps(config['dataset_directory'])
        outer_predictions = []; procedure = []; completed_outer = 0
        for repetition in range(len(config['seeds'])):
            for outer in range(config['outer_folds']):
                inner_predictions = []; completed_candidates = []
                parts = [p for p in partitions if p['repetition'] == repetition and p['outer_fold'] == outer]
                for part in parts:
                    progress.update(current_stage='preprocessing', repetition=repetition, outer_fold=outer,
                                    inner_fold=part['inner_fold'], candidate=None, setting=None,
                                    cache_status='checking', completed_candidates=completed_candidates)
                    cache, pre_directory, hit = prepare_features(root, digest(canonical(identity)), part, documents,
                        frame, numeric, lexical, saturated, lexicon, config)
                    progress.update(cache_status='preprocessing_hit' if hit else 'preprocessing_created',
                                    last_successful_checkpoint=str(pre_directory/'complete.json'))
                    if part['inner_fold'] is not None:
                        for candidate in CANDIDATES:
                            for c in settings(candidate, config):
                                prediction, _ = fit_candidate(root, cache, pre_directory, part, candidate, c, frame, config, progress)
                                inner_predictions.append(prediction)
                    else:
                        table, choices, winner = select_inner(pd.concat(inner_predictions, ignore_index=True), config['ap_tolerance'])
                        choice_directory = root/'selections'/f'r{repetition}_o{outer}'
                        write_table(choice_directory/'inner_metrics.parquet', table)
                        write_json(choice_directory/'choices.json', {'candidates': choices, 'overall': winner})
                        for choice in choices:
                            candidate = choice['candidate']; prediction, _ = fit_candidate(root, cache, pre_directory, part,
                                candidate, choice['C'], frame, config, progress)
                            prediction = prediction.assign(selected_fraction=choice['fraction'])
                            outer_predictions.append(prediction)
                            if candidate == winner['candidate']: procedure.append(prediction)
                            completed_candidates.append(candidate)
                            progress.update(completed_candidates=completed_candidates)
                    del cache
                completed_outer += 1
                if stop_after_outer is not None and completed_outer >= stop_after_outer:
                    progress.update(status='paused', current_stage='outer_partition_benchmark_complete')
                    return {'paused': True, 'completed_outer': completed_outer}
        predictions = pd.concat(outer_predictions, ignore_index=True)
        write_table(root/'outer_predictions.parquet', predictions)
        chosen = pd.concat(procedure, ignore_index=True)
        write_table(root/'selection_procedure_predictions.parquet', chosen)
        chosen_metrics, _, _, _ = aggregate_outer(chosen.assign(candidate='inner_selected_procedure'))
        write_table(root/'selection_procedure_metrics.parquet', chosen_metrics)
        report(root, predictions, frame, numeric, config, progress)
        # Revalidate frozen sources and authoritative fit count before completion.
        load_full_cohort(config['dataset_directory'])
        actual = sum(valid_checkpoint(p.parent) is not None for p in (root/'fits').glob('*/complete.json'))
        if actual != expected: raise ValueError(f'Expected {expected} fits, found {actual}')
        files = {str(path.relative_to(root)): file_hash(path) for path in root.rglob('*') if path.is_file()
                 and path.name not in ('progress.json', 'runtime_events.jsonl', 'run.log', 'complete.json') and not path.name.endswith('.tmp')}
        write_json(root/'complete.json', {'files': files, 'completed_fits': actual, 'expected_fits': expected,
            'peak_memory_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
            'elapsed_seconds': progress.offset+time.monotonic()-progress.started,
            'evaluation': 'development_only_no_production_fit'})
        progress.update(status='completed', current_stage='completed', completed_fits=actual,
                        completed_candidates=list(CANDIDATES), last_successful_checkpoint=str(root/'complete.json'))
        return json.loads((root/'complete.json').read_text())
    except BaseException as error:
        progress.failure(error)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/company_model_tuning.yaml')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--validate-only', action='store_true', help='Read-only contract/fold validation; no artifacts or fits')
    parser.add_argument('--stop-after-outer', type=int, help='Safe outer-partition benchmark boundary; resume later')
    args = parser.parse_args()
    config, _ = load_config(args.config)
    with threadpool_limits(limits=config['blas_threads']):
        run(args.config, args.resume, args.validate_only, args.stop_after_outer)


if __name__ == '__main__':
    main()
