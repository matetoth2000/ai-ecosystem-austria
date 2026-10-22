"""Content-overlap audit and deterministic group-balanced internal splits."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations

from .company_modeling_common import SPLITS, digest, tokens


class Components:
    def __init__(self, values):
        self.parent = {v: v for v in values}

    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def join(self, left, right):
        a, b = sorted((self.find(left), self.find(right)))
        self.parent[b] = a


def leakage_groups(source, config):
    settings = config['overlap']
    uf = Components([c['bvd_id'] for c in source['companies']])
    edges = []
    domain_companies, domain_content, content_domains = defaultdict(set), defaultdict(set), defaultdict(set)
    for r in source['relations']:
        domain_companies[r['website_id']].add(r['bvd_id'])
    for p in source['pages']:
        if p['usable']:
            domain_content[p['website_id']].add(p['content_id'])
            content_domains[p['content_id']].add(p['website_id'])

    def join_companies(a, b, reason, **details):
        uf.join(a, b)
        edges.append({'left_company': a, 'right_company': b, 'reason': reason, 'grouping': True, **details})

    def join_domains(a, b, reason, **details):
        for x in sorted(domain_companies[a]):
            for y in sorted(domain_companies[b]):
                if x != y:
                    join_companies(x, y, reason, **details)

    for domain, ids in sorted(domain_companies.items()):
        for a, b in combinations(sorted(ids), 2):
            join_companies(a, b, 'shared_normalized_domain')
    # A common final host alone can be a hosting/error service. Only accept it
    # when it is a registered evidence website and substantive content agrees.
    final_hosts = defaultdict(set)
    for p in source['pages']:
        if p['usable']:
            final_hosts[p['website_id']].add(p['final_host'])

    substantive, generic = set(), set()
    for cid, item in source['contents'].items():
        text = item['body_text'].casefold()
        if item['word_count'] >= settings['substantive_words']:
            substantive.add(cid)
            # Conservative quarantine of cross-site generic notices; no text is
            # deleted, and all exclusions are exposed in the content table.
            if item['word_count'] < settings['generic_maximum_words'] and any(m in text for m in settings['generic_markers']):
                generic.add(cid)
    substantive -= generic
    matched = defaultdict(lambda: [set(), set()])
    quarantine = set()
    for cid, domains in sorted(content_domains.items()):
        if len(domains) < 2:
            continue
        for a, b in combinations(sorted(domains), 2):
            if cid in substantive:
                matched[(a, b)][0].add(cid); matched[(a, b)][1].add(cid)
            else:
                quarantine.add(cid)
                edges.append({'left_company': None, 'right_company': None, 'reason': 'generic_or_short_cross_site_duplicate',
                              'grouping': False, 'left_domain': a, 'right_domain': b, 'content_id': cid})

    @lru_cache(maxsize=64)
    def shingles(cid):
        ts = tokens(source['contents'][cid]['body_text']); n = settings['shingle_size']
        return {hashlib.blake2b(' '.join(ts[i:i+n]).encode(), digest_size=8).digest() for i in range(max(0, len(ts)-n+1))}

    # Bottom-hash candidate fingerprints avoid an all-page-pairs comparison.
    # This is approximate candidate discovery; each retained edge is verified
    # against the complete shingle sets, not just the sketches.
    buckets = defaultdict(list)
    for cid in sorted(substantive):
        for fingerprint in sorted(shingles(cid))[:settings['fingerprint_size']]:
            buckets[fingerprint].append(cid)
    pairs = set(); skipped_buckets = 0
    for ids in buckets.values():
        if len(ids) > settings['maximum_fingerprint_bucket']:
            skipped_buckets += 1
            continue
        for a, b in combinations(ids, 2):
            if content_domains[a] != content_domains[b] or len(content_domains[a]) > 1:
                pairs.add((a, b))
    near_pairs = []
    for a, b in sorted(pairs):
        x, y = shingles(a), shingles(b)
        if not x or not y or min(len(x), len(y))/max(len(x), len(y)) < settings['jaccard']:
            continue
        similarity = len(x & y)/len(x | y)
        if similarity >= settings['jaccard']:
            near_pairs.append((a, b))
            for da in sorted(content_domains[a]):
                for db in sorted(content_domains[b]):
                    if da == db:
                        continue
                    left, right = sorted((da, db))
                    ca, cb = (a, b) if da == left else (b, a)
                    matched[(left, right)][0].add(ca); matched[(left, right)][1].add(cb)
    for (a, b), (ca, cb) in sorted(matched.items()):
        na, nb = len(domain_content[a] & substantive), len(domain_content[b] & substantive)
        coverage = len(ca)/na if na <= nb and na else len(cb)/nb if nb else 0
        single = na == nb == 1 and bool(ca & cb)
        material = min(len(ca), len(cb)) >= settings['material_pages'] and coverage >= settings['smaller_site_fraction']
        alias = (a in final_hosts[b] or b in final_hosts[a]) and material
        if material or single or alias:
            join_domains(a, b, 'verified_website_alias' if alias else 'material_content_overlap',
                         left_domain=a, right_domain=b, matching_pages=min(len(ca), len(cb)), smaller_coverage=coverage)
    # Known substantive overlap below the identity threshold must not leak.
    for (a, b), (ca, cb) in matched.items():
        if any(uf.find(x) != uf.find(y) for x in domain_companies[a] for y in domain_companies[b]):
            quarantine.update(ca | cb)
            edges.append({'left_company': None, 'right_company': None, 'reason': 'subthreshold_overlap_quarantined',
                          'grouping': False, 'left_domain': a, 'right_domain': b, 'matching_pages': min(len(ca), len(cb))})
    groups = {cid: 'group_' + digest(uf.find(cid))[:16] for cid in sorted(uf.parent)}
    return groups, edges, quarantine, {'near_candidate_pairs': len(pairs), 'verified_near_pairs': len(near_pairs),
                                     'common_fingerprint_buckets_skipped': skipped_buckets,
                                     'near_duplicate_search_exhaustive': False}


def balanced_assignment(members, categories, names, fractions, seed):
    total = len(members); totals = Counter(cat for cats in categories.values() for cat in cats)
    grouped = defaultdict(list)
    for cid, gid in members.items():
        grouped[gid].append(cid)
    counts = {s: Counter() for s in names}; sizes = Counter(); assigned = {}
    order = sorted(grouped, key=lambda g: (-len(grouped[g]), digest(f'{seed}:{g}')))
    for gid in order:
        ids = grouped[gid]; cats = Counter(c for cid in ids for c in categories[cid])
        def loss(s):
            index = names.index(s); f = fractions[index]
            # Incremental squared error makes large groups indivisible while
            # balancing source, label and evidence-state marginals.
            score = ((sizes[s]+len(ids)-total*f)**2-(sizes[s]-total*f)**2)/max(total*f, 1)
            for key, number in cats.items():
                target = totals[key]*f
                score += ((counts[s][key]+number-target)**2-(counts[s][key]-target)**2)/max(target, 1)
            return score, sizes[s]/f, index
        selected = min(names, key=loss)
        sizes[selected] += len(ids); counts[selected].update(cats)
        assigned.update({cid: selected for cid in ids})
    return assigned


def make_splits(source, groups, states, config):
    state_map = {s['bvd_id']: s['state'] for s in states}
    categories = {r['bvd_id']: [r['label'], 'source:'+r['calibration_source'], 'state:'+state_map[r['bvd_id']]] for r in source['labels']}
    assignment = balanced_assignment(groups, categories, list(SPLITS), config['split_fractions'], config['seed'])
    dev = {cid: gid for cid, gid in groups.items() if assignment[cid] == 'development'}
    k = config['development_folds']
    folds = balanced_assignment(dev, {cid: categories[cid] for cid in dev}, list(range(k)), [1/k]*k, config['seed'])
    return [{'bvd_id': cid, 'leakage_group_id': groups[cid], 'split': assignment[cid], 'development_fold': folds.get(cid)} for cid in sorted(groups)]
