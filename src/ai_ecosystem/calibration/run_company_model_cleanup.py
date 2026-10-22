"""One bounded cleanup comparison and two frozen development model bundles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import shutil
import sys
import time

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .company_cleanup_contract import (configuration, implementation_files, run_identity,
                                       validate_dataset, validate_parent)
from .company_cleanup_features import CANDIDATES, FrozenCompanyScorer, prepare_cleanup, write_joblib
from .company_cleanup_reporting import report
from .company_modeling_common import canonical, digest, file_hash
from .company_tuning_contract import Progress, valid_checkpoint, write_json, write_table
from .company_tuning_features import document_maps
from .company_tuning_metrics import aggregate_outer, paired_uncertainty, select_inner
from .run_company_model_tuning import fit_candidate


def complete_directory(directory, **metadata):
    directory = Path(directory)
    files = {str(p.relative_to(directory)): file_hash(p) for p in directory.rglob('*')
             if p.is_file() and p.name not in ('complete.json', 'progress.json', 'run.log', 'runtime_events.jsonl')
             and not p.name.endswith('.tmp')}
    write_json(directory/'complete.json', {'files': files, **metadata})


def reference_parity(root, predictions, parent):
    old = pd.read_parquet(Path(parent)/'outer_predictions.parquet')
    keys = ['bvd_id', 'repetition', 'outer_fold']
    old = old[old.candidate.eq('reference_logistic_size')].set_index(keys).sort_index()
    new = predictions[predictions.candidate.eq('reference_logistic_size')].set_index(keys).sort_index()
    old = old.loc[new.index]
    np.testing.assert_allclose(old.score, new.score, rtol=1e-11, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(old.selected_fraction, new.selected_fraction, atol=0, rtol=0)
    write_json(Path(root)/'reference_parity.json', {'rows': len(new),
        'maximum_score_difference': float((old.score-new.score).abs().max()),
        'operating_fractions_unchanged': True, 'parent': str(parent)})


def historical_correction(root, config, progress):
    directory = Path(root)/'historical_uncertainty_correction'
    if valid_checkpoint(directory): return
    directory.mkdir(parents=True, exist_ok=True)
    parent = Path(config['parent_directory']); predictions = pd.read_parquet(parent/'outer_predictions.parquet')
    progress.update(current_stage='historical_uncertainty_correction', candidate=None)
    metrics, _, _, _ = aggregate_outer(predictions)
    old = pd.read_parquet(parent/'outer_metrics.parquet')
    pd.testing.assert_frame_equal(metrics, old, check_exact=False, atol=1e-14, rtol=1e-14)
    corrected = paired_uncertainty(predictions, config)
    write_table(directory/'paired_intervals.parquet', corrected)
    previous = pd.read_parquet(parent/'paired_intervals.parquet')
    write_table(directory/'interval_changes.parquet', corrected.merge(previous, on=['candidate', 'metric'], suffixes=('_corrected', '_historical')))
    write_json(directory/'provenance.json', {'predictions_hash': file_hash(parent/'outer_predictions.parquet'),
        'historical_intervals_hash': file_hash(parent/'paired_intervals.parquet'),
        'point_estimates_unchanged': True, 'correction': 'integer_percentage_ceiling'})
    complete_directory(directory)


def freeze_models(root, identity, decision, documents, frame, numeric, lexical, lexicon, config, progress):
    root = Path(root); directory = root/'frozen'
    if valid_checkpoint(directory): return
    directory.mkdir(parents=True, exist_ok=True)
    progress.update(current_stage='final_full_development_preprocessing', candidate=None)
    part = {'repetition': 0, 'outer_fold': None, 'inner_fold': None,
            'train_ids': sorted(frame.bvd_id), 'evaluation_ids': sorted(frame.bvd_id)}
    cache, pre, _ = prepare_cleanup(root/'final_training', identity, part, documents, frame, numeric, lexical, lexicon, config)
    settings = json.loads((root/'reports/final_regularization.json').read_text())
    bundles = []
    for candidate in decision['frozen_candidates']:
        progress.update(current_stage='final_full_development_fit', candidate=candidate)
        prediction, fit_dir = fit_candidate(root/'final_training', cache, pre, part, candidate, settings[candidate],
            frame, config, progress, candidates=CANDIDATES)
        model = joblib.load(fit_dir/'model.joblib')
        used = CANDIDATES[candidate]
        fitted_names = {b.removesuffix('_mean') if b.endswith('_mean') else b for b in used}
        training = frame[frame.state.eq('PROCESSABLE') & frame.label.isin(['KEEP', 'EXCLUDE'])]
        provenance = {'candidate': candidate, 'C': settings[candidate], 'identity_hash': identity,
            'training_company_ids': training.bvd_id.tolist(), 'training_label_counts': training.label.value_counts().to_dict(),
            'preprocessing_company_ids': part['train_ids'], 'config': config, 'lexicon': lexicon,
            'status': 'development_fit_awaiting_independent_validation', 'production_threshold': None,
            'regularization_source': 'saved_inner_cv_only', 'role': 'primary' if candidate == decision['primary'] else 'comparator'}
        bundle = FrozenCompanyScorer(model, candidate, {b: cache['fitted'][b] for b in fitted_names},
                                     {b: cache['names'][b] for b in used}, provenance)
        output = directory/candidate; output.mkdir(parents=True, exist_ok=True)
        write_joblib(output/'model.joblib', bundle)
        write_json(output/'manifest.json', {**provenance, 'feature_order': bundle.names,
            'grounded_aliases': bundle.fitted['G3'].aliases if 'G3' in bundle.fitted else []})
        loaded = joblib.load(output/'model.joblib')
        scores = loaded.score(cache['ids'], {k: v.documents for k, v in documents.items()}, numeric, lexical,
                              frame.set_index('bvd_id').state)
        expected = prediction.set_index('bvd_id').loc[cache['ids'], 'score']
        np.testing.assert_allclose(scores.score, expected, rtol=1e-11, atol=1e-12, equal_nan=True)
        write_json(output/'reload_verification.json', {'rows': len(scores), 'passed': True,
            'maximum_score_difference': float(np.nanmax(abs(scores.score.to_numpy()-expected.to_numpy()))),
            'interpretation': 'serialization_check_not_performance_evaluation'})
        complete_directory(output); bundles.append(str(output/'model.joblib'))
    write_json(directory/'freeze_manifest.json', {**decision, 'bundles': bundles,
        'identity_hash': identity, 'no_further_tuning': True})
    complete_directory(directory)


def run(config_path, resume=False, validate_only=False, stop_after_outer=None, verification_output=None):
    config, lexicon = configuration(config_path)
    manifest, frame, numeric = validate_dataset(config['dataset_directory'])
    folds, parts, expected, parents, parent_identity = validate_parent(config, lexicon, frame, manifest)
    original_root = Path(config['output_directory'])
    if verification_output:
        if valid_checkpoint(original_root) is None: raise ValueError('Complete the primary run before verification')
        config['output_directory'] = str(verification_output)
        stop_after_outer = 1
    identity = run_identity(config, lexicon, manifest, parent_identity); identity_hash = digest(canonical(identity))
    if validate_only:
        result = {'companies': len(frame), 'labels': frame.label.value_counts().to_dict(),
                  'states': frame.state.value_counts().to_dict(), 'comparison_fits': expected,
                  'final_fits': 2, 'identity_hash': identity_hash}
        print(json.dumps(result, indent=2)); return result
    root = Path(config['output_directory'])
    if root.exists():
        if not resume: raise FileExistsError(root)
        if not (root/'identity.json').exists() or json.loads((root/'identity.json').read_text()) != identity:
            raise ValueError('Cleanup resume identity mismatch')
    else:
        root.mkdir(parents=True); write_json(root/'identity.json', identity)
        (root/'source_snapshot').mkdir()
        for name, path in implementation_files().items(): shutil.copyfile(path, root/'source_snapshot'/name)
        write_json(root/'config_snapshot.json', config); write_json(root/'lexicon_snapshot.json', lexicon)
    done = valid_checkpoint(root)
    if done:
        print('Completed cleanup verified; no refitting.'); return done
    progress = Progress(root, expected+2)
    prior_final = sum(valid_checkpoint(p.parent) is not None for p in (root/'final_training/fits').glob('*/complete.json'))
    progress.update(completed_fits=progress.state['completed_fits']+prior_final,
                    reused_fits=0, reused_preprocessing=0, expected_comparison_fits=expected, expected_final_fits=2)
    try:
        write_table(root/'cohort.parquet', frame); write_table(root/'fold_assignments.parquet', folds)
        write_json(root/'schedule.json', {'partitions': parts, 'expected_comparison_fits': expected, 'expected_final_fits': 2})
        write_json(root/'cohort_audit.json', {'companies': len(frame), 'groups': frame.leakage_group_id.nunique(),
            'labels': frame.label.value_counts().to_dict(), 'states': frame.state.value_counts().to_dict(),
            'historical_splits_are_development_only': True})
        parent_features = Path(config['parent_directory'])/'deterministic_features'
        if valid_checkpoint(parent_features) is None: raise ValueError('Missing parent deterministic features')
        dest = root/'deterministic_features'; dest.mkdir(exist_ok=True)
        for name in ('lexical.parquet', 'visibility_mismatches.parquet', 'source_count_anomalies.json'):
            if not (dest/name).exists(): shutil.copyfile(parent_features/name, dest/name)
            if file_hash(dest/name) != file_hash(parent_features/name): raise ValueError('Deterministic feature mismatch')
        lexical = pd.read_parquet(dest/'lexical.parquet').set_index('bvd_id')
        documents = document_maps(config['dataset_directory'])
        outer = []; procedure = []; count = 0
        for repetition in range(len(config['seeds'])):
            for fold in range(config['outer_folds']):
                inner = []
                for part in [p for p in parts if p['repetition'] == repetition and p['outer_fold'] == fold]:
                    progress.update(current_stage='cleanup_preprocessing', repetition=repetition, outer_fold=fold,
                                    inner_fold=part['inner_fold'], candidate=None)
                    key = canonical([part['train_ids'], sorted(set(part['train_ids']+part['evaluation_ids']))])
                    parent = None if verification_output else parents.get(key)
                    if not verification_output and parent is None: raise ValueError('Missing coherent parent preprocessing')
                    cache, pre, hit = prepare_cleanup(root, identity_hash, part, documents, frame, numeric, lexical, lexicon, config, parent)
                    progress.update(cache_status='cleanup_hit' if hit else 'cleanup_created',
                        reused_preprocessing=progress.state['reused_preprocessing']+int(parent is not None),
                        last_successful_checkpoint=str(pre/'complete.json'))
                    if part['inner_fold'] is not None:
                        for candidate in CANDIDATES:
                            for c in ([10.] if candidate == 'reference_logistic_size' else config['logistic_c']):
                                prediction, _ = fit_candidate(root, cache, pre, part, candidate, c, frame, config, progress, candidates=CANDIDATES)
                                inner.append(prediction)
                    else:
                        table, choices, winner = select_inner(pd.concat(inner, ignore_index=True), config['ap_tolerance'])
                        choice_dir = root/'selections'/f'r{repetition}_o{fold}'
                        write_table(choice_dir/'inner_metrics.parquet', table)
                        write_json(choice_dir/'choices.json', {'candidates': choices, 'overall': winner})
                        for choice in choices:
                            prediction, _ = fit_candidate(root, cache, pre, part, choice['candidate'], choice['C'], frame, config, progress, candidates=CANDIDATES)
                            prediction = prediction.assign(selected_fraction=choice['fraction']); outer.append(prediction)
                            if choice['candidate'] == winner['candidate']: procedure.append(prediction)
                    del cache
                count += 1
                partial = pd.concat(outer, ignore_index=True)
                write_table(root/'partial_outer_predictions.parquet', partial)
                reference_parity(root, partial, config['parent_directory'])
                if stop_after_outer is not None and count >= stop_after_outer:
                    if verification_output:
                        saved = pd.read_parquet(original_root/'outer_predictions.parquet')
                        saved = saved[saved.repetition.eq(0) & saved.outer_fold.eq(0)]
                        keys = ['candidate', 'bvd_id']; a = partial.set_index(keys).sort_index(); b = saved.set_index(keys).sort_index()
                        np.testing.assert_allclose(a.score, b.score, rtol=1e-10, atol=1e-11, equal_nan=True)
                        np.testing.assert_array_equal(a.C, b.C); np.testing.assert_array_equal(a.selected_fraction, b.selected_fraction)
                        for name in ('choices.json', 'inner_metrics.parquet'):
                            target = Path('selections/r0_o0')/name
                            if name.endswith('.json'):
                                if json.loads((root/target).read_text()) != json.loads((original_root/target).read_text()):
                                    raise ValueError('Reproduced inner choices differ')
                            else:
                                pd.testing.assert_frame_equal(pd.read_parquet(root/target), pd.read_parquet(original_root/target), atol=1e-12, rtol=1e-12)
                        write_json(root/'reproducibility.json', {'passed': True, 'parent': str(original_root),
                            'fresh_preprocessing': True, 'comparison_rows': len(a), 'outer_partitions': 1})
                    progress.update(status='paused', current_stage='outer_partition_verified' if verification_output else 'outer_partition_complete')
                    return {'paused': True, 'completed_outer': count}
        predictions = pd.concat(outer, ignore_index=True); chosen = pd.concat(procedure, ignore_index=True)
        write_table(root/'outer_predictions.parquet', predictions)
        write_table(root/'selection_procedure_predictions.parquet', chosen)
        decision = report(root, predictions, chosen, numeric, config, progress)
        historical_correction(root, config, progress)
        freeze_models(root, identity_hash, decision, documents, frame, numeric, lexical, lexicon, config, progress)
        shutil.copyfile(root/'reports/comparison_report.md', root/'comparison_report.md')
        validate_dataset(config['dataset_directory'])
        if valid_checkpoint(Path(config['parent_directory'])) is None: raise ValueError('Parent no longer valid')
        completed = sum(valid_checkpoint(p.parent) is not None for p in (root/'fits').glob('*/complete.json'))
        final = sum(valid_checkpoint(p.parent) is not None for p in (root/'final_training/fits').glob('*/complete.json'))
        if completed != expected or final != 2: raise ValueError('Cleanup fit count mismatch')
        complete_directory(root, comparison_fits=completed, final_fits=final, executed_fits=completed+final,
            reused_fits=0, imported_preprocessing_partitions=len(parts),
            elapsed_seconds=progress.offset+time.monotonic()-progress.started,
            peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform == 'darwin' else 1024),
            status='frozen_development_models_awaiting_independent_validation')
        progress.update(status='completed', current_stage='frozen_for_independent_validation',
                        completed_fits=completed+final, completed_candidates=list(CANDIDATES),
                        last_successful_checkpoint=str(root/'complete.json'))
        return json.loads((root/'complete.json').read_text())
    except BaseException as error:
        progress.failure(error); raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/company_model_cleanup.yaml')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--stop-after-outer', type=int)
    parser.add_argument('--verification-output', help='Fresh first-outer reproduction directory; requires a completed primary run')
    args = parser.parse_args(); config, _ = configuration(args.config)
    with threadpool_limits(limits=config['blas_threads']):
        run(args.config, args.resume, args.validate_only, args.stop_after_outer, args.verification_output)


if __name__ == '__main__':
    main()
