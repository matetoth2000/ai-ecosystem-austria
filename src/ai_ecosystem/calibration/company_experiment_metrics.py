"""Company-ranking metrics. UNKNOWN never supplies a binary negative label."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score


def ranked(frame):
    return frame.loc[frame.state.eq('PROCESSABLE') & frame.score.notna()].sort_values(
        ['score', 'bvd_id'], ascending=[False, True], kind='stable')


def group_weights(frame):
    sizes = frame.groupby('leakage_group_id').bvd_id.transform('size')
    return 1 / sizes.to_numpy(dtype=float)


def summary(frame, weighted=False):
    ordered = ranked(frame)
    decidable = ordered.loc[ordered.label.isin(['KEEP', 'EXCLUDE'])]
    y = decidable.label.eq('KEEP').to_numpy(dtype=int)
    weights = group_weights(decidable) if weighted and len(decidable) else None
    ap = float(average_precision_score(y, decidable.score, sample_weight=weights)) if len(set(y)) == 2 else None
    auc = float(roc_auc_score(y, decidable.score, sample_weight=weights)) if len(set(y)) == 2 else None
    result = {'average_precision': ap, 'roc_auc': auc, 'operational_count': len(frame),
              'processable_count': len(ordered), 'no_evidence_count': int(frame.state.eq('NO_EVIDENCE').sum()),
              'known_keep_count': int(frame.label.eq('KEEP').sum()),
              'unknown_processable_count': int(ordered.label.eq('UNKNOWN').sum())}
    weights_all = group_weights(ordered) if weighted and len(ordered) else np.ones(len(ordered))
    positive = ordered.label.eq('KEEP').to_numpy() * weights_all
    for target in (0.8, 0.9, 0.95):
        count = int(np.searchsorted(np.cumsum(positive), target*positive.sum())+1) if positive.sum() else None
        result[f'selection_fraction_for_{int(target*100)}_capture'] = count/len(ordered) if count else None
    cutoff = math.ceil(0.2*len(ordered))
    result['keep_capture_at_20_percent'] = float(positive[:cutoff].sum()/positive.sum()) if positive.sum() else None
    return result


def operating_points(frame, config):
    ordered = ranked(frame)
    rows = []
    total_keep = int(frame.label.eq('KEEP').sum())
    for view, denominator in [('operational', len(frame)), ('processable', len(ordered))]:
        requests = [('fraction', f, math.ceil(f*denominator)) for f in config['fractions']]
        requests += [('absolute', k, k) for k in config['absolute_k'] if k <= denominator]
        for kind, requested, count in requests:
            selected = ordered.iloc[:count]
            counts = selected.label.value_counts()
            keep, unknown, exclude = (int(counts.get(v, 0)) for v in ('KEEP', 'UNKNOWN', 'EXCLUDE'))
            rows.append({'cohort': view, 'budget_type': kind, 'budget': requested, 'requested_count': count,
                'selected_count': len(selected), 'cohort_size': denominator,
                'selected_share': len(selected)/denominator if denominator else None,
                'keep': keep, 'unknown': unknown, 'exclude': exclude,
                'keep_capture': keep/total_keep if total_keep else None, 'missed_keep': total_keep-keep,
                'known_keep_fraction': keep/len(selected) if len(selected) else None,
                'decidable_precision': keep/(keep+exclude) if keep+exclude else None,
                'selected_websites': selected.normalized_domain.nunique(),
                'selected_leakage_groups': selected.leakage_group_id.nunique(),
                'no_evidence_deferred': int(frame.state.eq('NO_EVIDENCE').sum()),
                'cost_multiplier_per_company': len(selected)})
    return rows


def capture_curve(frame):
    ordered = ranked(frame)
    total = int(frame.label.eq('KEEP').sum())
    counts = dict(KEEP=0, UNKNOWN=0, EXCLUDE=0)
    rows = []
    for k, row in enumerate(ordered.itertuples(), 1):
        counts[row.label] += 1
        keep, exclude = counts['KEEP'], counts['EXCLUDE']
        rows.append({'selected_count': k, 'selected_share': k/len(ordered), **counts,
                     'keep_capture': keep/total if total else None,
                     'decidable_precision': keep/(keep+exclude) if keep+exclude else None})
    return rows


def paired_bootstrap(frames, config, reference):
    """Identical group draws for all candidates, retaining repeated clusters."""
    first = frames[reference]
    groups = sorted(first.leakage_group_id.unique())
    rng = np.random.default_rng(config['seed'])
    metrics = ['average_precision', 'keep_capture_at_20_percent', 'selection_fraction_for_90_capture']
    results = {name: {m: [] for m in metrics} for name in frames}
    positions = {name: {g: np.flatnonzero(f.leakage_group_id.to_numpy() == g) for g in groups} for name, f in frames.items()}
    for _ in range(config['bootstrap_replicates']):
        draw = rng.choice(groups, len(groups), replace=True)
        for name, f in frames.items():
            sample = f.iloc[np.concatenate([positions[name][g] for g in draw])]
            values = summary(sample)
            for metric in metrics:
                results[name][metric].append(values[metric])
    rows = []
    for name in frames:
        for metric in metrics:
            values = np.array([np.nan if v is None else v for v in results[name][metric]], dtype=float)
            ref = np.array([np.nan if v is None else v for v in results[reference][metric]], dtype=float)
            good = np.isfinite(values); paired = good & np.isfinite(ref)
            ci = np.quantile(values[good], [.025, .975]) if good.any() else [None, None]
            delta = np.quantile((values-ref)[paired], [.025, .975]) if paired.any() else [None, None]
            rows.append({'candidate': name, 'metric': metric, 'lower': ci[0], 'upper': ci[1],
                         'valid_replicates': int(good.sum()), 'reference': reference,
                         'paired_delta_lower': delta[0], 'paired_delta_upper': delta[1]})
    return rows


def choose(rows, tolerance):
    valid = [r for r in rows if r['average_precision'] is not None]
    if not valid:
        raise ValueError('No evaluable candidates')
    best = max(r['average_precision'] for r in valid)
    tied = [r for r in valid if best-r['average_precision'] <= tolerance]
    return min(tied, key=lambda r: (r['selection_fraction_for_90_capture'] if r['selection_fraction_for_90_capture'] is not None else float('inf'),
                                    r['complexity'], r.get('feature_dimensions', 0), r['candidate'], r.get('setting_id', '')))
