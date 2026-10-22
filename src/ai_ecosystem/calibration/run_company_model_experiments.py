"""Compare cheap company rankers, then evaluate one frozen internal holdout."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import resource
import sys
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from .company_modeling_common import canonical, digest, file_hash
from .company_experiment_features import CANDIDATES, feature_blocks, implementation_hashes, load_dataset, matrix, prepare_fold
from .company_experiment_metrics import capture_curve, choose, group_weights, operating_points, paired_bootstrap, ranked, summary


def json_value(value):
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(json_value(value), ensure_ascii=False, indent=2, sort_keys=True)+'\n')
    temp.replace(path)


def save_table(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    frame.to_csv(path, index=False)


def load_experiment_config(path):
    config = yaml.safe_load(Path(path).read_text())
    required = ('experiment_id','dataset_directory','output_directory','seed','logistic_c','tree_leaves',
                'tree_minimum_samples','tree_iterations','tree_learning_rate','tree_l2','maximum_iterations',
                'ap_tolerance','minimum_text_groups','bootstrap_replicates','random_seeds','blas_threads',
                'fractions','absolute_k','capture_targets')
    if not isinstance(config, dict) or any(k not in config for k in required):
        raise ValueError('Incomplete experiment configuration')
    if config['minimum_text_groups'] != 2 or config['bootstrap_replicates'] < 1 or config['random_seeds'] < 1:
        raise ValueError('Invalid experiment safeguards')
    if any(not 0 < f <= 1 for f in config['fractions']) or any(c <= 0 for c in config['logistic_c']):
        raise ValueError('Invalid model grid or budget fractions')
    return config


def environment():
    import sklearn, scipy
    return {'python': sys.version, 'sklearn': sklearn.__version__, 'scipy': scipy.__version__,
            'pandas': pd.__version__, 'numpy': np.__version__}


def peak_memory():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


def settings(candidate, config):
    if candidate == 'tree_numeric':
        return [{'leaves': leaf, 'minimum_samples': size} for leaf, size in itertools.product(config['tree_leaves'],config['tree_minimum_samples'])]
    return [{'C': c} for c in config['logistic_c']]


def complexity(candidate, setting):
    return setting['leaves'] + 1/setting['minimum_samples'] if candidate == 'tree_numeric' else setting['C']


def fit_predict(cache, candidate, setting, pooling, training, evaluation, config):
    x = matrix(cache, candidate, pooling); positions = {cid: i for i, cid in enumerate(cache['ids'])}
    supervised = training[training.state.eq('PROCESSABLE') & training.label.isin(['KEEP','EXCLUDE'])]
    if set(supervised.label) != {'KEEP','EXCLUDE'}:
        raise ValueError('Training fold lacks both decidable classes')
    indexes = [positions[c] for c in supervised.bvd_id]
    y = supervised.label.eq('KEEP').to_numpy(dtype=int)
    weights = group_weights(supervised); weights /= weights.mean()
    if candidate == 'tree_numeric':
        model = HistGradientBoostingClassifier(max_leaf_nodes=setting['leaves'], min_samples_leaf=setting['minimum_samples'],
            max_iter=config['tree_iterations'],learning_rate=config['tree_learning_rate'],l2_regularization=config['tree_l2'],
            early_stopping=False, random_state=config['seed'])
    else:
        model = LogisticRegression(C=setting['C'], solver='liblinear', max_iter=config['maximum_iterations'], random_state=config['seed'])
    start = time.monotonic()
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        model.fit(x[indexes], y, sample_weight=weights)
    fitted_seconds = time.monotonic()-start
    scored = evaluation.copy(); scored['score'] = np.nan
    eligible = scored.state.eq('PROCESSABLE')
    rows = [positions[c] for c in scored.loc[eligible,'bvd_id']]
    start = time.monotonic()
    if rows:
        scored.loc[eligible,'score'] = model.predict_proba(x[rows])[:,1] if candidate == 'tree_numeric' else model.decision_function(x[rows])
    inference_seconds = time.monotonic()-start
    scored['candidate'] = candidate
    benchmark = {'candidate': candidate, 'setting': canonical(setting), 'training_rows': len(supervised),
        'training_keep': int(y.sum()), 'training_exclude': int(len(y)-y.sum()), 'training_unknown': 0,
        'training_no_evidence': 0, 'training_seconds': fitted_seconds, 'inference_seconds': inference_seconds,
        'inference_companies_per_second': len(rows)/inference_seconds if inference_seconds else None,
        'feature_dimensions': x.shape[1], 'peak_memory_bytes': peak_memory()}
    return model, scored, benchmark


def cached_fit(root, cache, candidate, setting, pooling, training, evaluation, config):
    identity = {'preprocessing': cache['cache_key'], 'candidate': candidate, 'setting': setting,
                'pooling': pooling, 'evaluation_ids': evaluation.bvd_id.tolist()}
    directory = root/'fits'/digest(canonical(identity))
    if (directory/'complete.json').exists():
        record = json.loads((directory/'complete.json').read_text())
        if record['identity'] != identity or file_hash(directory/'predictions.parquet') != record['predictions_hash']:
            raise ValueError('Invalid fitted result cache')
        return pd.read_parquet(directory/'predictions.parquet'), record['benchmark']
    model, scored, benchmark = fit_predict(cache,candidate,setting,pooling,training,evaluation,config)
    directory.mkdir(parents=True,exist_ok=True)
    scored.to_parquet(directory/'predictions.parquet',index=False)
    joblib.dump(model,directory/'model.joblib',compress=3)
    write_json(directory/'complete.json',{'identity':identity,'benchmark':benchmark,'predictions_hash':file_hash(directory/'predictions.parquet')})
    return scored, benchmark


def baseline(frame, numeric, seed=None):
    result = frame.copy()
    if seed is None:
        result['score'] = [2*numeric.loc[c,'lexical_explicit_ai_presence']+numeric.loc[c,'lexical_adjacent_presence'] for c in result.bvd_id]
    else:
        result['score'] = [int(digest(f'{seed}:{c}')[:13],16)/16**13 for c in result.bvd_id]
    result.loc[~result.state.eq('PROCESSABLE'),'score'] = np.nan
    return result


def diagnostics(frames, numeric):
    subgroups, examples = [], []
    for candidate, frame in frames.items():
        frame = frame.merge(numeric[['availability_unique_contents','relationship_shared',
            'language_de_proportion','language_en_proportion','language_missing_proportion']],left_on='bvd_id',right_index=True)
        frame['site_size'] = pd.cut(frame.availability_unique_contents,[-1,5,20,100,float('inf')],labels=['<=5','6-20','21-100','>100']).astype(str)
        frame['language'] = np.where(frame.language_de_proportion >= .5,'de',np.where(frame.language_en_proportion >= .5,'en',np.where(frame.language_missing_proportion >= .5,'missing','other_or_mixed')))
        frame['visibility_gap'] = frame.current_pages_not_model_visible.gt(0)
        for field in ('relationship_shared','site_size','language','calibration_source','visibility_gap'):
            for value, subset in frame.groupby(field,dropna=False):
                subgroups.append({'candidate':candidate,'subgroup':field,'value':str(value),'groups':subset.leakage_group_id.nunique(),
                                  'small_subgroup_caution':subset.leakage_group_id.nunique()<10, **summary(subset)})
        order = ranked(frame)
        for name, subset in [('low_keep',order[order.label.eq('KEEP')].tail(10)),('high_exclude',order[order.label.eq('EXCLUDE')].head(10)),
                             ('high_unknown',order[order.label.eq('UNKNOWN')].head(10)),('low_unknown',order[order.label.eq('UNKNOWN')].tail(10))]:
            for row in subset.to_dict('records'):
                examples.append({**row,'case_type':name,'candidate':candidate})
        unknown = order[order.label.eq('UNKNOWN')]
        for q in (0,.1,.25,.5,.75,.9,1):
            subgroups.append({'candidate':candidate,'subgroup':'unknown_score_quantile','value':str(q),
                              'score':float(unknown.score.quantile(q)) if len(unknown) else None,'unknown_count':len(unknown)})
    return subgroups, examples


def evaluate_frames(directory, frames, numeric, config, reference):
    directory.mkdir(parents=True,exist_ok=True)
    metrics, budgets, curves = [], [], []
    for name, frame in frames.items():
        metrics.append({'candidate':name,**summary(frame)})
        metrics.append({'candidate':name,'weighting':'group_balanced',**summary(frame,weighted=True)})
        budgets.extend({'candidate':name,**r} for r in operating_points(frame,config))
        curves.extend({'candidate':name,**r} for r in capture_curve(frame))
    save_table(directory/'metrics.csv',metrics)
    save_table(directory/'operating_points.csv',budgets)
    save_table(directory/'capture_curves.csv',curves)
    save_table(directory/'bootstrap_intervals.csv',paired_bootstrap(frames,config,reference))
    subgroup, examples = diagnostics(frames,numeric)
    save_table(directory/'subgroups.csv',subgroup); save_table(directory/'error_examples.csv',examples)
    predictions = pd.concat([f.assign(candidate=name) for name,f in frames.items()],ignore_index=True)
    predictions.to_parquet(directory/'predictions.parquet',index=False)
    orderings = pd.DataFrame({name: ranked(f).set_index('bvd_id').score.rank(pct=True) for name,f in frames.items()})
    orderings['rank_spread'] = orderings.max(axis=1)-orderings.min(axis=1)
    save_table(directory/'disagreements.csv',orderings.sort_values('rank_spread',ascending=False).reset_index())
    return metrics


def random_analysis(directory, cohort, numeric, config):
    rows, budgets = [], []
    for seed in range(config['random_seeds']):
        f = baseline(cohort,numeric,config['seed']+seed)
        rows.append({'seed':config['seed']+seed,**summary(f)})
        budgets.extend({'seed':config['seed']+seed,**r} for r in operating_points(f,config))
    save_table(directory/'random_seed_metrics.csv',rows); save_table(directory/'random_seed_budgets.csv',budgets)
    frame = pd.DataFrame(rows)
    distribution = {}
    for key in ('average_precision','selection_fraction_for_90_capture','keep_capture_at_20_percent'):
        distribution[key] = {'mean':frame[key].mean(),'median':frame[key].median(),'p025':frame[key].quantile(.025),'p975':frame[key].quantile(.975)}
    write_json(directory/'random_distribution.json',distribution)


def compare(config_path, resume=False):
    config = load_experiment_config(config_path); root = Path(config['output_directory']); start=time.monotonic()
    manifest, frame, numeric = load_dataset(config['dataset_directory'])
    identity = {'config':config,'dataset_hash':manifest['canonical_build_hash'],'implementation':implementation_hashes(),'environment':environment()}
    if root.exists():
        if not resume:
            raise FileExistsError(f'Use --resume for existing experiment: {root}')
        if json.loads((root/'identity.json').read_text()) != identity:
            raise ValueError('Experiment identity changed; use a new experiment version')
        if (root/'comparison_complete.json').exists():
            return json.loads((root/'selection_freeze.json').read_text())
    else:
        root.mkdir(parents=True); write_json(root/'identity.json',identity)
        write_json(root/'config_snapshot.json',config)
        write_json(root/'dataset_verification.json',{'dataset_hash':manifest['canonical_build_hash'],'feature_blocks':feature_blocks(numeric.columns),'holdout_targets_opened':False})
        save_table(root/'split_assignments.csv',frame[['bvd_id','leakage_group_id','split','development_fold','state']])
    dev = frame[frame.split.eq('development')].copy(); selection=frame[frame.split.eq('model_selection')].copy()
    cv_records=[]; predictions=[]; benchmarks=[]; preprocessing=[]
    folds = sorted(dev.development_fold.unique()); selected_settings={}; pooling='mean'
    stages = [list(CANDIDATES)[:4]+['tree_numeric'],['logistic_combined','logistic_grounded','logistic_size']]
    for stage, candidates in enumerate(stages):
        for fold in folds:
            training=dev[dev.development_fold.ne(fold)]; validation=dev[dev.development_fold.eq(fold)]
            print(f'CV stage {stage+1}, fold {int(fold)}: preprocessing',flush=True)
            cache=prepare_fold(config['dataset_directory'],training.bvd_id.tolist(),dev.bvd_id.tolist(),numeric,frame,config,root/'preprocessing')
            preprocessing.append({'stage':f'cv_{stage}_{fold}','cache_key':cache['cache_key'],'cache_hit':cache['cache_hit'],
                'seconds':cache['preprocessing_seconds'],'warm_cache_seconds':cache.get('warm_cache_seconds'), **cache['timings']})
            for candidate in candidates:
                for setting in settings(candidate,config):
                    scored,bench=cached_fit(root,cache,candidate,setting,pooling,training,validation,config)
                    setting_id=canonical(setting)
                    cv_records.append({'candidate':candidate,'setting_id':setting_id,'fold':int(fold),'complexity':complexity(candidate,setting),'feature_dimensions':bench['feature_dimensions'],**summary(scored)})
                    predictions.append(scored.assign(setting_id=setting_id,fold=int(fold)))
                    benchmarks.append({'stage':f'cv_{fold}',**bench})
            del cache
        for candidate in candidates:
            candidate_rows=[]
            for setting in settings(candidate,config):
                rows=[r for r in cv_records if r['candidate']==candidate and r['setting_id']==canonical(setting)]
                candidate_rows.append({'candidate':candidate,'setting_id':canonical(setting),'complexity':complexity(candidate,setting),
                    'average_precision':float(np.mean([r['average_precision'] for r in rows])),
                    'selection_fraction_for_90_capture':float(np.mean([r['selection_fraction_for_90_capture'] for r in rows])),
                    'fold_ap_std':float(np.std([r['average_precision'] for r in rows]))})
            selected_settings[candidate]=choose(candidate_rows,config['ap_tolerance'])
        if stage==0:
            mean=selected_settings['logistic_text_mean']['average_precision'];maximum=selected_settings['logistic_text_max']['average_precision']
            pooling='max' if maximum-mean>=config['ap_tolerance'] else 'mean'
            write_json(root/'pooling_freeze.json',{'pooling':pooling,'text_mean_cv_ap':mean,'text_max_cv_ap':maximum,'source':'development_cv_only'})
        save_table(root/'cv_metrics.csv',cv_records)
    write_json(root/'candidate_settings.json',selected_settings)
    pd.concat(predictions,ignore_index=True).to_parquet(root/'cv_predictions.parquet',index=False)
    # Diagnose out-of-fold errors, without presenting pooled CV AP as primary.
    oof={}
    all_predictions=pd.concat(predictions,ignore_index=True)
    for name,spec in selected_settings.items():
        oof[name]=all_predictions[all_predictions.candidate.eq(name)&all_predictions.setting_id.eq(spec['setting_id'])].copy()
    subgroup,examples=diagnostics(oof,numeric)
    save_table(root/'development_subgroups.csv',subgroup);save_table(root/'development_error_examples.csv',examples)
    print('Development CV complete; fitting frozen candidates for model-selection',flush=True)
    train_ids=dev.bvd_id.tolist();ids=frame.loc[~frame.split.eq('internal_holdout'),'bvd_id'].tolist()
    cache=prepare_fold(config['dataset_directory'],train_ids,ids,numeric,frame,config,root/'preprocessing')
    preprocessing.append({'stage':'model_selection','cache_key':cache['cache_key'],'cache_hit':cache['cache_hit'],'seconds':cache['preprocessing_seconds'],**cache['timings']})
    frames={};selection_rows=[]
    for name,spec in selected_settings.items():
        setting=json.loads(spec['setting_id'])
        scored,bench=cached_fit(root,cache,name,setting,pooling,dev,selection,config)
        frames[name]=scored
        selection_rows.append({'candidate':name,'setting_id':spec['setting_id'],'complexity':complexity(name,setting),'feature_dimensions':bench['feature_dimensions'],**summary(scored)})
        benchmarks.append({'stage':'model_selection',**bench})
    winner=choose(selection_rows,config['ap_tolerance'])
    frames['baseline_fixed_lexical']=baseline(selection,numeric)
    frames['baseline_random']=baseline(selection,numeric,config['seed'])
    print(f'Model-selection winner: {winner["candidate"]}; computing uncertainty and diagnostics',flush=True)
    evaluate_frames(root/'model_selection',frames,numeric,config,winner['candidate'])
    random_analysis(root/'model_selection',selection,numeric,config)
    save_table(root/'runtime_benchmarks.csv',benchmarks);save_table(root/'preprocessing_benchmarks.csv',preprocessing)
    payload={'winner':winner,'pooling':pooling,'candidate_settings':selected_settings,'experiment_identity_hash':digest(canonical(identity)),
             'dataset_hash':manifest['canonical_build_hash'],'selection_metrics_hash':file_hash(root/'model_selection/metrics.csv'),
             'policy':'mean CV AP, 0.01 tolerance, then effort for 90% KEEP capture, then simplicity; holdout cannot tune',
             'holdout_targets_opened':False}
    freeze={'payload':payload,'freeze_hash':digest(canonical(json_value(payload)))}
    write_json(root/'selection_freeze.json',freeze)
    write_report(root,winner,selection_rows,config)
    write_json(root/'comparison_complete.json',{'runtime_seconds':time.monotonic()-start,'peak_memory_bytes':peak_memory(),'freeze_hash':freeze['freeze_hash']})
    return freeze


def verified_freeze(root, config, manifest):
    root=Path(root)
    if not (root/'comparison_complete.json').exists():
        raise ValueError('Comparison must finish before holdout targets can be opened')
    freeze=json.loads((root/'selection_freeze.json').read_text());payload=freeze['payload']
    identity=json.loads((root/'identity.json').read_text())
    if (freeze['freeze_hash']!=digest(canonical(payload)) or payload['dataset_hash']!=manifest['canonical_build_hash']
        or payload['experiment_identity_hash']!=digest(canonical(identity)) or identity['config']!=config
        or identity['implementation']!=implementation_hashes()
        or payload['selection_metrics_hash']!=file_hash(root/'model_selection/metrics.csv')):
        raise ValueError('Invalid selection freeze; refusing holdout access')
    return freeze


def evaluate_holdout(config_path):
    config=load_experiment_config(config_path);root=Path(config['output_directory'])
    manifest,frame,numeric=load_dataset(config['dataset_directory'])
    freeze=verified_freeze(root,config,manifest); output=root/'holdout'
    if (output/'complete.json').exists():
        raise FileExistsError('Holdout was already evaluated; refusing another evaluation')
    if (output/'targets_opened.json').exists():
        raise RuntimeError('Holdout targets were already opened during an interrupted evaluation; inspect artifacts before recovery')
    output.mkdir(exist_ok=True)
    training=frame[~frame.split.eq('internal_holdout')];evaluation=frame[frame.split.eq('internal_holdout')].copy()
    print('Refitting frozen winner without holdout targets',flush=True)
    cache=prepare_fold(config['dataset_directory'],training.bvd_id.tolist(),frame.bvd_id.tolist(),numeric,frame,config,root/'preprocessing')
    winner=freeze['payload']['winner'];candidate=winner['candidate'];setting=json.loads(winner['setting_id'])
    model,scored,benchmark=fit_predict(cache,candidate,setting,freeze['payload']['pooling'],training,evaluation,config)
    joblib.dump({'model':model,'preprocessing':cache['fitted'],'candidate':candidate,'pooling':freeze['payload']['pooling'],
                 'feature_blocks':feature_blocks(numeric.columns),'freeze_hash':freeze['freeze_hash']},output/'frozen_winner.joblib',compress=3)
    scored.to_parquet(output/'predictions_before_targets.parquet',index=False)
    write_json(output/'targets_opened.json',{'freeze_hash':freeze['freeze_hash'],'predictions_hash':file_hash(output/'predictions_before_targets.parquet')})
    target_path=Path(config['dataset_directory'])/manifest['holdout_targets']
    if file_hash(target_path)!=manifest['tables']['evaluation_only/internal_holdout_targets']['file_hash']:
        raise ValueError('Holdout target hash mismatch')
    targets=pd.read_parquet(target_path)
    scored=scored.drop(columns=['label','website_target_eligible']).merge(targets,on='bvd_id',validate='one_to_one')
    if len(scored)!=len(evaluation) or scored.label.isna().any():
        raise ValueError('Incomplete holdout target join')
    frames={candidate:scored,'baseline_fixed_lexical':baseline(scored,numeric),'baseline_random':baseline(scored,numeric,config['seed'])}
    evaluate_frames(output,frames,numeric,config,candidate);random_analysis(output,scored,numeric,config)
    write_json(output/'runtime.json',{'model':benchmark,'preprocessing_seconds':cache['preprocessing_seconds'],'timings':cache['timings']})
    write_json(output/'complete.json',{'freeze_hash':freeze['freeze_hash'],'metrics':summary(scored),'model_hash':file_hash(output/'frozen_winner.joblib'),
                                    'no_post_holdout_tuning':True})
    write_report(root,winner,None,config,summary(scored))
    return summary(scored)


def write_report(root,winner,selection_rows,config,holdout=None):
    path=root/'comparison_report.md'
    if holdout is not None:
        with path.open('a') as stream:
            stream.write('\n## One-Time Internal Holdout\n\n')
            stream.write(f"- Frozen winner: `{winner['candidate']}`. No post-holdout tuning.\n")
            for key,value in holdout.items():
                stream.write(f'- {key}: {value}\n')
        return
    lines=['# Company AI-Relevance Model Comparison','',
           'These are company-level cost-reduction rankings, not final AI-active decisions or automatic exclusions.','',
           '## Model-Selection Results','', '| Candidate | Decidable AP | Fraction for 90% KEEP capture |','|---|---:|---:|']
    for r in sorted(selection_rows,key=lambda r:-r['average_precision']):
        lines.append(f"| {r['candidate']} | {r['average_precision']:.4f} | {r['selection_fraction_for_90_capture']:.4f} |")
    lines.extend(['',f"Frozen winner: **{winner['candidate']}**. AP within {config['ap_tolerance']} is resolved by selection effort, then simplicity.",'',
        '## Interpretation and Limits','',
        '- UNKNOWN never supplies a negative training label; its selection counts and score distribution are reported separately.',
        '- NO_EVIDENCE is deferred with no score, not classified as EXCLUDE.',
        '- Operating points are comparisons within enriched samples, not production-population thresholds or prevalence estimates.',
        '- Positive labels overwhelmingly come from the enriched campaign; source subgroup comparisons cannot establish broad positive recall.',
        '- Labels reflect historical visible packages. Visibility-gap and shared-domain diagnostics are included; neither raw text nor name overlap proves focal-company attribution.',
        '- This internal holdout has prior exploratory exposure and is not independent population validation.',
        '- Inspect operating_points.csv, bootstrap_intervals.csv, subgroups.csv, error_examples.csv and disagreements.csv before interpreting small differences.',
        '- Random results cover 100 deterministic seeds. Monetary budgets remain selected_count multiplied by a later estimated per-company cost; domains are not automatically interchangeable packages.',
        '- No production threshold, new labels, API calls, or population inference were used.'])
    path.write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['compare','evaluate-holdout'])
    parser.add_argument('--config',default='config/company_model_experiments.yaml')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();config=load_experiment_config(args.config)
    with threadpool_limits(limits=config['blas_threads']):
        result=compare(args.config,args.resume) if args.command=='compare' else evaluate_holdout(args.config)
    print(json.dumps(json_value(result),indent=2))


if __name__=='__main__':
    main()
