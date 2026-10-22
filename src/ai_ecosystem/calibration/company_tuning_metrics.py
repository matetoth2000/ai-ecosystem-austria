"""Operational ranking metrics; unresolved selections incur cost, not labels."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


def selection_count(size, percent):
    """Ceiling of an integer percentage without floating-point boundary drift."""
    if int(size) != size or size < 0 or int(percent) != percent or not 0 <= percent <= 100:
        raise ValueError('Expected nonnegative size and integer percentage in [0, 100]')
    return (int(size) * int(percent) + 99) // 100


def operational_curve(frame, weighted=False):
    frame = frame[frame.state.eq('PROCESSABLE') & frame.score.notna()].sort_values(
        ['score', 'bvd_id'], ascending=[False, True], kind='stable')
    n = len(frame)
    weights = 1/frame.groupby('leakage_group_id').bvd_id.transform('size').to_numpy() if weighted else np.ones(n)
    labels = frame.label.to_numpy()
    known = np.isin(labels, ['KEEP', 'EXCLUDE'])
    ap_weights = None
    if weighted and known.any():
        kf = frame.iloc[np.flatnonzero(known)]
        ap_weights = 1/kf.groupby('leakage_group_id').bvd_id.transform('size').to_numpy()
    ap = float(average_precision_score(labels[known] == 'KEEP', frame.score.to_numpy()[known], sample_weight=ap_weights)) if len(set(labels[known])) == 2 else None
    sums = {label: np.r_[0., np.cumsum(weights*(labels == label))] for label in ('KEEP', 'UNKNOWN', 'EXCLUDE')}
    raw = {label: np.r_[0, np.cumsum(labels == label)] for label in sums}
    total_keep = sums['KEEP'][-1]
    efforts = {}
    for target in (.8, .9, .95):
        efforts[f'effort_{int(100*target)}'] = (int(np.searchsorted(sums['KEEP'], target*total_keep)) / n) if n and total_keep else None
    rows = []
    for percent in range(1, 101):
        count = selection_count(n, percent)
        keep, unknown, exclude = (sums[label][count] for label in sums)
        precision = keep/(keep+unknown+exclude) if keep+unknown+exclude else 0.
        recall = keep/total_keep if total_keep else 0.
        standard = keep/(keep+exclude) if keep+exclude else 0.
        f1 = lambda p: 2*p*recall/(p+recall) if p+recall else 0.
        row = {'fraction': percent/100, 'average_precision': ap,
            'operational_precision': precision, 'keep_recall': recall, 'operational_f1': f1(precision),
            'standard_precision': standard, 'standard_f1': f1(standard),
            'selected_count': count, 'selected_share': count/n if n else 0.,
            'eligible_count': n, 'eligible_keep': int((labels == 'KEEP').sum()),
            'missed_keep': int((labels == 'KEEP').sum()-raw['KEEP'][count]),
            'no_evidence_deferred': None, 'weighted': weighted, **efforts}
        for label in sums:
            row['selected_'+label.lower()] = int(raw[label][count])
            row['selected_'+label.lower()+'_share'] = float(raw[label][count]/count) if count else 0.
        rows.append(row)
    return pd.DataFrame(rows)


def at_fraction(frame, fraction, weighted=False):
    return operational_curve(frame, weighted).iloc[int(round(fraction*100))-1].to_dict()


def select_inner(predictions, tolerance):
    rows = []
    for (candidate, c), group in predictions.groupby(['candidate', 'C'], sort=True):
        curves = [operational_curve(f) for _, f in group.groupby('inner_fold')]
        means = pd.concat(curves).groupby('fraction', as_index=False).mean(numeric_only=True)
        best = means.sort_values(['operational_f1', 'selected_share', 'fraction'], ascending=[False, True, True]).iloc[0]
        rows.append({'candidate': candidate, 'C': float(c), 'average_precision': float(best.average_precision),
                     'operational_f1': float(best.operational_f1), 'selected_share': float(best.selected_share),
                     'fraction': float(best.fraction),
                     'feature_dimensions': float(group.feature_dimensions.mean()) if 'feature_dimensions' in group else 0.})
    table = pd.DataFrame(rows)
    def choose(subset):
        near = subset[subset.average_precision >= subset.average_precision.max()-tolerance]
        return near.sort_values(['operational_f1', 'selected_share', 'C', 'feature_dimensions', 'candidate'], ascending=[False, True, True, True, True]).iloc[0].to_dict()
    selected = [choose(group) for _, group in table.groupby('candidate', sort=False)]
    return table, selected, choose(pd.DataFrame(selected))


def aggregate_outer(predictions):
    rows, curves = [], []
    for (rep, fold, candidate), group in predictions.groupby(['repetition', 'outer_fold', 'candidate'], sort=True):
        fraction = float(group.selected_fraction.iloc[0])
        for weighted in (False, True):
            curve = operational_curve(group, weighted)
            curve['no_evidence_deferred'] = int(group.state.eq('NO_EVIDENCE').sum())
            metadata = {'repetition': rep, 'outer_fold': fold, 'candidate': candidate, 'weighted': weighted}
            row = curve.iloc[int(round(fraction*100))-1].to_dict()
            rows.append({**row, **metadata})
            curves.append(curve.assign(**metadata))
    metrics = pd.DataFrame(rows)
    repetition = metrics.groupby(['candidate', 'weighted', 'repetition'], as_index=False).mean(numeric_only=True)
    summary = repetition.groupby(['candidate', 'weighted'], as_index=False).mean(numeric_only=True)
    return metrics, pd.concat(curves, ignore_index=True), repetition, summary


def paired_uncertainty(predictions, config):
    """Bootstrap the same group multiplicities across all folds/repeats/models."""
    p = predictions[predictions.state.eq('PROCESSABLE') & predictions.score.notna()]
    groups = sorted(p.leakage_group_id.unique()); positions = {g: i for i, g in enumerate(groups)}
    parts = []
    for (candidate, rep, fold), f in p.groupby(['candidate', 'repetition', 'outer_fold']):
        f = f.sort_values(['score', 'bvd_id'], ascending=[False, True])
        parts.append((candidate, f.label.to_numpy(), f.score.to_numpy(),
                      np.array([positions[g] for g in f.leakage_group_id]), float(f.selected_fraction.iloc[0])))
    rng = np.random.default_rng(config['bootstrap_seed']); samples = []
    for draw in range(config['bootstrap_replicates']):
        multiplicities = rng.multinomial(len(groups), np.ones(len(groups))/len(groups))
        measures = {}
        for candidate, labels, scores, group_indices, fraction in parts:
            weights = multiplicities[group_indices]
            # Expanding group multiplicities preserves tied-score ordering and
            # integer selection budgets without treating repetitions as new firms.
            indexes = np.repeat(np.arange(len(labels)), weights)
            ls, sc = labels[indexes], scores[indexes]
            known = np.isin(ls, ['KEEP', 'EXCLUDE']); total = int((ls == 'KEEP').sum())
            if len(set(ls[known])) < 2 or not total: continue
            ap = float(average_precision_score(ls[known] == 'KEEP', sc[known]))
            count = selection_count(len(ls), round(fraction*100)); keep = int((ls[:count] == 'KEEP').sum())
            precision = keep/count if count else 0.; recall = keep/total
            f1 = 2*precision*recall/(precision+recall) if precision+recall else 0.
            measures.setdefault(candidate, []).append([ap, f1, count/len(ls), recall])
        for candidate, values in measures.items():
            samples.append({'replicate': draw, 'candidate': candidate, **dict(zip(
                ['average_precision', 'operational_f1', 'selected_share', 'keep_recall'], np.mean(values, axis=0)))})
    table = pd.DataFrame(samples); reference = table[table.candidate.eq('reference_logistic_size')].set_index('replicate')
    rows = []
    for candidate, values in table.groupby('candidate'):
        values = values.set_index('replicate')
        for metric in ('average_precision', 'operational_f1', 'selected_share', 'keep_recall'):
            common = values.index.intersection(reference.index)
            delta = values.loc[common, metric]-reference.loc[common, metric]
            lo, hi = np.quantile(values[metric], [.025, .975]); dl, dh = np.quantile(delta, [.025, .975])
            rows.append({'candidate': candidate, 'metric': metric, 'lower': lo, 'upper': hi,
                         'paired_delta_lower': dl, 'paired_delta_upper': dh, 'valid_replicates': len(common),
                         'interpretation': 'conditional_on_fitted_development_models'})
    return pd.DataFrame(rows)
