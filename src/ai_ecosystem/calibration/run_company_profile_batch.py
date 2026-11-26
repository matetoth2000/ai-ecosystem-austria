"""Offline scored-sample preparation and shared, explicit paid Batch operations."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from ai_ecosystem.extraction.config import load_extraction_config
from .company_ai_profile_contract import (QUOTE_VALIDATION_VERSION, canonical, digest,
                                          prompt_and_schema, request_body)
from .company_ai_profile_packages import _visible, load_config, package_company
from .company_ai_profile_tokens import local_tokens
from .company_modeling_common import file_hash
from .company_modeling_sources import load_sources
from .compare_ai_profile_budgets import batch_cost
from .prepare_ai_profile_validation_batch import (MAX_FILE_BYTES, MAX_REQUESTS, QUEUE_HEADROOM,
    TARGET_FILE_BYTES, TARGET_FILE_TOKENS, atomic_json, pack_files)
from .score_company_sample import sample_company_ids

WORKFLOW = 'scored_company_sample_v1'
MODEL = 'cleaned_grounded_combined'
EXACT_CUTOFF = Decimal('-0.10212121864474141')
POLICY = 'data/interim/calibration/company_score_analysis/independent_validation_holdout_v1/policy_freeze.json'
CONFIG = 'config/company_ai_profile_pilot_v5.yaml'
TOKEN_METHOD = 'o200k_base_local_proxy_not_luna_verified'


def require_tokenizer_cache(config):
    """Fail offline instead of letting tiktoken download during preparation/review."""
    if config['token_counting']['allocation_encoding'] != 'o200k_base':
        raise ValueError('Expected the existing o200k_base allocation proxy')
    cache = os.environ.get('TIKTOKEN_CACHE_DIR', os.environ.get('DATA_GYM_CACHE_DIR',
                           str(Path(tempfile.gettempdir()) / 'data-gym-cache')))
    url = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
    path = Path(cache) / hashlib.sha1(url.encode()).hexdigest()
    if (not cache or not path.is_file() or file_hash(path) !=
            '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'):
        raise ValueError('Populate the persistent o200k_base cache during setup; this command is offline')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def policy_value(path):
    raw = json.loads(Path(path).read_text(encoding='utf-8'), parse_float=Decimal)
    options = [p for p in raw['policies'] if p['budget_usd'] == 200]
    if (raw['model'] != MODEL or raw['score_rule'] != 'score >= cutoff; include all ties'
            or len(options) != 1 or Decimal(str(options[0]['cutoff'])) != EXACT_CUTOFF):
        raise ValueError('Expected the exact frozen $200-scenario policy')
    return {'model': MODEL, 'scenario_usd': 200, 'cutoff': str(options[0]['cutoff']),
            'score_rule': raw['score_rule'], 'source_sha256': file_hash(path),
            'interpretation': 'selection rule; not a guaranteed spending cap'}


def select_scores(path, members, cutoff):
    """Missing scores stay unlabelled; foreign/duplicate/inconsistent rows fail."""
    scores = {}
    members = sorted(members)
    if len(set(members)) != len(members):
        raise ValueError('Duplicate sample members')
    member_set = set(members)
    with Path(path).open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        names = reader.fieldnames or []
        if len(set(names)) != len(names) or not {'bvd_id', 'state', MODEL} <= set(names):
            raise ValueError('Missing or duplicate score columns')
        for row in reader:
            cid = row['bvd_id']
            if (None in row or cid not in member_set or cid in scores
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', cid or '')):
                raise ValueError(f'Duplicate, malformed or out-of-sample score row: {cid}')
            state = row['state']
            if state not in {'PROCESSABLE', 'NO_EVIDENCE'}:
                raise ValueError(f'Invalid evidence state: {cid}')
            value = (row[MODEL] or '').strip()
            try:
                score = Decimal(value) if value else None
            except InvalidOperation as exc:
                raise ValueError(f'Invalid score: {cid}') from exc
            if score is not None and score.is_nan():
                score = None
            if score is not None and not score.is_finite():
                raise ValueError(f'Nonfinite score: {cid}')
            if state == 'NO_EVIDENCE' and score is not None:
                raise ValueError(f'NO_EVIDENCE company has a score: {cid}')
            if 'reference_logistic_size' in row and (row['reference_logistic_size'] or '').strip():
                try:
                    reference = Decimal(row['reference_logistic_size'])
                except InvalidOperation as exc:
                    raise ValueError(f'Invalid reference score: {cid}') from exc
                if reference.is_infinite() or (state == 'NO_EVIDENCE' and not reference.is_nan()):
                    raise ValueError(f'Inconsistent reference score: {cid}')
            selected = state == 'PROCESSABLE' and score is not None and score >= cutoff
            reason = ('NO_EVIDENCE' if state == 'NO_EVIDENCE' else 'missing_score' if score is None
                      else 'at_or_above_policy_cutoff' if selected else 'below_policy_cutoff')
            scores[cid] = {'company_id': cid, 'source_state': state,
                           'score': str(score) if score is not None else None,
                           'selected': selected, 'selection_reason': reason}
    return [scores.get(cid, {'company_id': cid, 'source_state': None, 'score': None,
                            'selected': False, 'selection_reason': 'missing_score_row'}) for cid in members]


def immutable(path, content):
    path = Path(path)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f'Frozen preparation changed: {path.name}')
    else:
        with path.open('xb') as handle:
            handle.write(content)


def frozen_json(path, value):
    immutable(path, (canonical(value) + '\n').encode('utf-8'))


def preparation_summary(manifest, records, config, identity, files):
    tokens = sum(r['estimated_input_tokens'] for r in records.values())
    costs = [batch_cost(r['estimated_input_tokens'], config['max_output_tokens'], config) for r in records.values()]
    sensitivity = sum(sum(batch_cost(math.ceil(r['estimated_input_tokens'] * QUEUE_HEADROOM),
                                    config['max_output_tokens'], config)) for r in records.values())
    coverage = Counter()
    for row in manifest:
        coverage.update(row.get('coverage', {}))
    blockers = []
    if sensitivity > identity['spend_allowance_usd']:
        blockers.append('20%-higher-input cost estimate exceeds per-run spending allowance')
    if math.ceil(tokens * QUEUE_HEADROOM) >= identity['queue_allowance_tokens']:
        blockers.append('Estimated queued tokens with headroom exceed declared queue allowance')
    return {'workflow': WORKFLOW, 'sample_id': identity['sample_id'], 'policy': identity['policy'],
            'sample_companies': len(manifest), 'selected': sum(r['selected'] for r in manifest),
            'not_selected': sum(not r['selected'] for r in manifest),
            'screened_out_below_cutoff': sum(r['selection_reason'] == 'below_policy_cutoff' for r in manifest),
            'unscored': sum(r['selection_reason'] in {'NO_EVIDENCE', 'missing_score', 'missing_score_row'} for r in manifest),
            'selection_reasons': dict(Counter(r['selection_reason'] for r in manifest)),
            'selected_without_request': sum(r['selected'] and r['profiling_status'] != 'included' for r in manifest),
            'included': len(records), 'coverage': dict(coverage),
            'estimated_input_tokens': tokens, 'queue_headroom_tokens': math.ceil(tokens * QUEUE_HEADROOM),
            'queue_allowance_tokens': identity['queue_allowance_tokens'],
            'spend_allowance_usd': identity['spend_allowance_usd'],
            'batch_input_usd': sum(c[0] for c in costs), 'batch_output_reserved_usd': sum(c[1] for c in costs),
            'batch_total_reserved_usd': sum(sum(c) for c in costs),
            'twenty_percent_more_input_cost_sensitivity_usd': sensitivity,
            'token_estimator': TOKEN_METHOD, 'verified_model_token_counts': False,
            'profile_model': config['model'], 'reasoning_effort': config['reasoning_effort'],
            'website_evidence_token_allowance': config['website_evidence_token_cap'], 'pricing': config['pricing'],
            'output_tokens_reserved_per_request': config['max_output_tokens'],
            'contract_id': config['contract_id'], 'mapping_version': config['mapping_version'],
            'quote_validation_version': QUOTE_VALIDATION_VERSION,
            'file_limits': {'maximum_bytes': MAX_FILE_BYTES, 'maximum_requests': MAX_REQUESTS,
                            'target_bytes': TARGET_FILE_BYTES, 'target_input_tokens': TARGET_FILE_TOKENS},
            'files': files, 'submission_blocked': bool(blockers), 'blockers': blockers,
            'cost_note': 'Recorded Luna Batch rates and local token estimates, not a bill or provider-enforced cap; '
                         'cache writes, tokenization, retries and account usage can change actual cost.'}


def prepare(sample_id, scores, output, *, spend_allowance_usd, queue_allowance_tokens,
            root=None, database=None, policy=None, resume=False):
    root = Path(root or find_project_root())
    output, scores = Path(output), Path(scores)
    if output.exists() and not resume:
        raise FileExistsError(f'Run already exists: {output}; use --resume to verify/reuse it')
    if (not math.isfinite(spend_allowance_usd) or spend_allowance_usd <= 0
            or type(queue_allowance_tokens) is not int or queue_allowance_tokens <= 0):
        raise ValueError('Explicit positive spending and queue allowances are required')
    database = Path(database or resolve_database_path(root))
    members = sample_company_ids(database, sample_id)
    policy_path = Path(policy or root / POLICY)
    chosen_policy = policy_value(policy_path)
    manifest = select_scores(scores, members, Decimal(chosen_policy['cutoff']))
    config = load_config(root, root / CONFIG)
    if (config['contract_id'], config['mapping_version'], config['model'], config['reasoning_effort'],
        config['website_evidence_token_cap'], config['max_output_tokens']) != (
            'company_ai_profile_v4', 'company_ai_profile_mapping_v4', 'gpt-6-luna', 'low', 250000, 8000):
        raise ValueError('Approved profile settings changed')
    config = {**config, 'sample_id': sample_id, 'spend_allowance_usd': float(spend_allowance_usd)}
    config.pop('pilot_spend_allowance_usd', None)
    prompt, schema = prompt_and_schema(config['contract_id'])
    extraction_version = load_extraction_config(root).extraction_version
    if extraction_version != 'v1':
        raise ValueError('Frozen sample scoring requires v1 extracted evidence')
    selected = [r['company_id'] for r in manifest if r['selected']]
    if selected:
        require_tokenizer_cache(config)
    # Unlabelled path: no historical datasets, validation cohorts, splits or review decisions.
    source = load_sources(database, root, {'extraction_version': extraction_version}, company_ids=selected) if selected else {}
    if source.get('errors'):
        raise ValueError(f'Selected source evidence failed to load: {source["errors"][:3]}')
    identity = {'workflow': WORKFLOW, 'sample_id': sample_id, 'members': sorted(members),
                'scores_sha256': file_hash(scores), 'policy': chosen_policy,
                'config_sha256': digest(config), 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'schema_sha256': digest(schema), 'extraction_version': extraction_version,
                'profile_config_source_sha256': file_hash(root / CONFIG),
                'spend_allowance_usd': float(spend_allowance_usd), 'queue_allowance_tokens': queue_allowance_tokens,
                'source_sha256': digest(source), 'page_ordering': 'root_about_product_service_solution_then_depth_page_id_v1'}
    if output.exists():
        if read_json(output / 'preparation_identity.json') != identity:
            raise ValueError('Resume identity/source mismatch; preserve this run and use a new directory')
        if (output / 'complete.json').exists():
            return validate_prepared(output)[0]
    else:
        output.mkdir(parents=True)
    frozen_json(output / 'preparation_identity.json', identity)
    immutable(output / 'input_scores.csv', scores.read_bytes())
    immutable(output / 'policy_freeze.json', policy_path.read_bytes())
    frozen_json(output / 'config_snapshot.json', config)
    frozen_json(output / 'schema_snapshot.json', schema)
    immutable(output / 'prompt_snapshot.txt', prompt.encode())
    for name in ('work', 'packages'):
        (output / name).mkdir(exist_ok=True)
    companies = {c['bvd_id']: c for c in source.get('companies', [])}
    records = {}
    overhead = local_tokens(prompt, config) + local_tokens(canonical(schema), config) + 10 if selected else 0
    for row in manifest:
        cid = row['company_id']
        row['profiling_status'] = 'not_selected'
        if not row['selected']:
            continue
        company = {'company_id': cid, **companies[cid]}
        if not company['normalized_domain']:
            row['profiling_status'] = 'no_usable_evidence'
            continue
        package = package_company(company, source, root, config)
        row['coverage'] = package['coverage']
        frozen_json(output / 'packages' / f'{cid}.json', package)
        if not package['pages']:
            row['profiling_status'] = 'no_usable_evidence'
            continue
        body = request_body(package, config, prompt, schema)
        estimate = overhead + local_tokens(canonical(_visible(package)), config)
        if math.ceil(estimate * QUEUE_HEADROOM) + config['max_output_tokens'] > config['max_context_tokens']:
            raise ValueError(f'Estimated request context exceeded: {cid}')
        request_hash = digest(body)
        record = {'identity_hash': digest(identity), 'company_id': cid, 'request_hash': request_hash,
                  'estimated_input_tokens': estimate, 'included_pages': len(package['pages']),
                  'omitted_pages': package['coverage']['omitted_pages'],
                  'batch_line': {'custom_id': f'company-profile-{cid}-{request_hash[:16]}',
                                 'method': 'POST', 'url': '/v1/responses', 'body': body}}
        frozen_json(output / 'work' / f'{cid}.json', record)
        records[cid] = record
        row['profiling_status'] = 'included'
    immutable(output / 'company_manifest.jsonl', ''.join(canonical(row)+'\n' for row in manifest).encode())
    files = pack_files(list(records.values()), output)
    summary = preparation_summary(manifest, records, config, identity, files)
    frozen_json(output / 'summary.json', summary)
    paths = [p for p in output.rglob('*') if p.is_file() and p.name != 'complete.json']
    frozen_json(output / 'complete.json', {'files': {str(p.relative_to(output)): file_hash(p) for p in sorted(paths)}})
    validate_prepared(output)
    report(output)
    return summary


def validate_prepared(directory):
    """Validate frozen local files without source DB, CSV or external services."""
    directory = Path(directory)
    marker = read_json(directory / 'complete.json')
    for name, expected in marker['files'].items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()) or file_hash(path) != expected:
            raise ValueError(f'Frozen file hash mismatch: {name}')
    identity = read_json(directory / 'preparation_identity.json')
    config = read_json(directory / 'config_snapshot.json')
    schema = read_json(directory / 'schema_snapshot.json')
    prompt = (directory / 'prompt_snapshot.txt').read_text(encoding='utf-8')
    if (identity['workflow'] != WORKFLOW or policy_value(directory / 'policy_freeze.json') != identity['policy']
            or file_hash(directory / 'input_scores.csv') != identity['scores_sha256']
            or digest(config) != identity['config_sha256'] or digest(schema) != identity['schema_sha256']
            or hashlib.sha256(prompt.encode()).hexdigest() != identity['prompt_sha256']):
        raise ValueError('Frozen preparation identity mismatch')
    manifest = [json.loads(line) for line in (directory / 'company_manifest.jsonl').read_text().splitlines()]
    selections = select_scores(directory / 'input_scores.csv', identity['members'], Decimal(identity['policy']['cutoff']))
    if len(manifest) != len(selections) or any(any(row[k] != expected[k] for k in expected)
                                               for row, expected in zip(manifest, selections)):
        raise ValueError('Frozen selection membership changed')
    records = {}
    expected_ids = {r['company_id'] for r in manifest if r['profiling_status'] == 'included' and r['selected']}
    if expected_ids:
        require_tokenizer_cache(config)
    overhead = local_tokens(prompt, config) + local_tokens(canonical(schema), config) + 10 if expected_ids else 0
    for path in sorted((directory / 'work').glob('*.json')):
        record = read_json(path); cid = record['company_id']
        package = read_json(directory / 'packages' / f'{cid}.json')
        line = record['batch_line']
        expected = request_body(package, config, prompt, schema)
        if (cid in records or cid not in expected_ids or path.name != f'{cid}.json'
                or record['identity_hash'] != digest(identity) or package['company_id'] != cid
                or not package['pages'] or line['body'] != expected or digest(expected) != record['request_hash']
                or record['estimated_input_tokens'] != overhead + local_tokens(canonical(_visible(package)), config)
                or line['custom_id'] != f'company-profile-{cid}-{record["request_hash"][:16]}'
                or line['method'] != 'POST' or line['url'] != '/v1/responses'):
            raise ValueError(f'Frozen request identity mismatch: {cid}')
        records[cid] = record
    if set(records) != expected_ids:
        raise ValueError('Incomplete frozen requests')
    summary = read_json(directory / 'summary.json')
    files = {Path(item['path']).name: item for item in summary['files']}
    if len(files) != len(summary['files']):
        raise ValueError('Duplicate Batch file names')
    by_custom = {r['batch_line']['custom_id']: r for r in records.values()}; seen = set()
    for name, item in files.items():
        path = directory / name
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        if (file_hash(path) != item['sha256'] or path.stat().st_size != item['bytes']
                or item['bytes'] > MAX_FILE_BYTES or len(lines) != item['requests'] or len(lines) > MAX_REQUESTS):
            raise ValueError(f'Invalid frozen Batch file: {name}')
        total = 0
        for line in lines:
            key = line['custom_id']
            if key in seen or key not in by_custom or line != by_custom[key]['batch_line']:
                raise ValueError(f'Unknown/duplicate frozen request: {key}')
            seen.add(key); total += by_custom[key]['estimated_input_tokens']
        if total != item['estimated_input_tokens']:
            raise ValueError('Batch token estimate mismatch')
    if seen != set(by_custom) or summary != preparation_summary(manifest, records, config, identity, summary['files']):
        raise ValueError('Frozen summary/Batch coverage mismatch')
    return summary, records, files


def report(directory):
    """Join coverage, unresolved outcomes and recorded-usage costs; never label exclusions."""
    from .analyze_validation_usage_costs import usage_cost
    directory = Path(directory)
    summary, records, _ = validate_prepared(directory)
    manifest = [json.loads(line) for line in (directory / 'company_manifest.jsonl').read_text().splitlines()]
    results = {p.stem: read_json(p) for p in (directory / 'results').glob('*.json')}
    if not set(results) <= set(records):
        raise ValueError('Unexpected company result')
    rows, statuses, labels, usage_totals = [], Counter(), Counter(), Counter()
    unknown_cost = []
    for item in manifest:
        cid = item['company_id']; record = records.get(cid); result = results.get(cid)
        status = item['profiling_status']
        label, calculated, cost_note = None, None, None
        if record:
            status = result['status'] if result else 'pending'
            if result:
                if result['company_id'] != cid or result['request_hash'] != record['request_hash'] or result['custom_id'] != record['batch_line']['custom_id']:
                    raise ValueError(f'Result identity mismatch: {cid}')
                label = result['derived']['label'] if status == 'valid' else None
                usage = result.get('usage')
                if usage:
                    try:
                        costs = usage_cost(usage)
                        calculated = costs['total_cost_usd']; usage_totals.update(costs)
                    except (ValueError, KeyError, TypeError) as exc:
                        cost_note = f'Usage cost unavailable: {exc}'
                else:
                    cost_note = 'No recorded usage; cost unknown, not zero'
            if calculated is None:
                unknown_cost.append(cid)
        statuses[status] += 1
        if label is not None:
            labels[label] += 1
        rows.append({'company_id': cid, 'selected': item['selected'], 'selection_reason': item['selection_reason'],
                     'score': item['score'], 'status': status, 'label': label,
                     'coverage': item.get('coverage'), 'usage': result.get('usage') if result else None,
                     'calculated_usage_cost_usd': calculated, 'cost_note': cost_note,
                     'validation_detail': result.get('detail') if result else None,
                     'request_hash': record['request_hash'] if record else None,
                     'returned_model': result.get('returned_model') if result else None})
    result = {'preparation': summary, 'status_counts': dict(statuses), 'valid_labels': dict(labels),
              'unresolved_selected': sum(r['selected'] and r['status'] != 'valid' for r in rows),
              'calculated_usage_totals': dict(usage_totals), 'cost_unknown_company_ids': unknown_cost,
              'company_results': rows,
              'interpretation': 'Unselected, pending, failed, missing and invalid companies have no negative label. '
                                'Usage-based costs use recorded rates, not an invoice; reasoning is included in output tokens.'}
    atomic_json(directory / 'report.json', result)
    with (directory / 'company_results.csv').open('w', encoding='utf-8', newline='') as handle:
        fields = ['company_id', 'selected', 'selection_reason', 'score', 'status', 'label',
                  'coverage', 'usage', 'calculated_usage_cost_usd', 'cost_note', 'validation_detail',
                  'request_hash', 'returned_model']
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    text = ['# Scored company sample profiling', '',
            f"Sample: {summary['sample_id']}; cutoff: {summary['policy']['cutoff']} (include ties); "
            f"profile model: {summary['profile_model']} ({summary['reasoning_effort']}).",
            f"Selected {summary['selected']}/{summary['sample_companies']}; requests {summary['included']}; "
            f"not selected {summary['not_selected']}; selected without request {summary['selected_without_request']}.",
            f"Selection reasons: {summary['selection_reasons']}; unscored companies are not negative labels.",
            f"Evidence coverage: {summary['coverage']}", f"Outcome statuses: {dict(statuses)}; valid labels: {dict(labels)}.",
            f"Unresolved selected companies: {result['unresolved_selected']}.",
            f"Estimated Batch cost with full 8k output allowance: ${summary['batch_total_reserved_usd']:.6f}; "
            f"20%-higher-input sensitivity: ${summary['twenty_percent_more_input_cost_sensitivity_usd']:.6f}; "
            f"explicit run allowance: ${summary['spend_allowance_usd']:.2f}.",
            f"Estimated input: {summary['estimated_input_tokens']}; queue headroom: {summary['queue_headroom_tokens']}; "
            f"declared queue allowance: {summary['queue_allowance_tokens']}.",
            f"Files: {len(summary['files'])}; limits/packing targets: {summary['file_limits']}.",
            f"Calculated usage-based cost with known usage: ${usage_totals.get('total_cost_usd', 0):.6f}; "
            f"unknown-cost requests: {len(unknown_cost)}. See report.json for per-company usage/outcomes.",
            'Local o200k_base counts are estimates, not Luna-verified counts. No token-count calls were made.',
            summary['cost_note'], result['interpretation'],
            f"Submission blocked: {summary['submission_blocked']}; blockers: {summary['blockers']}.",
            'The $200 scenario selects companies; it is not a $200 run budget or a provider billing cap.',
            'Batch processing is asynchronous; return later for status/ingest. No automatic failed-request resubmission.']
    (directory / 'report.md').write_text('\n\n'.join(text)+'\n', encoding='utf-8')
    return result


def main():
    from . import run_ai_profile_validation_batch as engine
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--sample-id', required=True)
    prep.add_argument('--scores', type=Path, required=True)
    prep.add_argument('--spend-allowance-usd', type=float, required=True)
    prep.add_argument('--queue-allowance-tokens', type=int, required=True,
                      help='Current user-verified account queued-input allowance; not inherited from the old study')
    prep.add_argument('--database', type=Path)
    prep.add_argument('--policy', type=Path, help='Frozen policy_freeze.json; defaults to the saved holdout policy')
    prep.add_argument('--resume', action='store_true')
    sub.add_parser('dry-run')
    submit_parser = sub.add_parser('submit')
    submit_parser.add_argument('--confirm-paid', action='store_true')
    submit_parser.add_argument('--file')
    sub.add_parser('status'); sub.add_parser('ingest'); sub.add_parser('report')
    recovery = sub.add_parser('reconcile')
    recovery.add_argument('--file', required=True)
    recovery.add_argument('--confirm-remote-absent', action='store_true')
    args = parser.parse_args(); root = find_project_root()
    directory = args.run_dir if args.run_dir.is_absolute() else root / args.run_dir
    if args.command == 'prepare':
        result = prepare(args.sample_id, args.scores, directory, spend_allowance_usd=args.spend_allowance_usd,
                         queue_allowance_tokens=args.queue_allowance_tokens, database=args.database,
                         policy=args.policy, resume=args.resume, root=root)
    else:
        # Never let this entry point silently operate on a historical validation run.
        summary = validate_prepared(directory)[0]
        if args.command == 'dry-run':
            result = summary
        elif args.command == 'report':
            result = report(directory)
        elif args.command == 'submit':
            print(json.dumps(summary, indent=2), flush=True)  # Counts, omissions, costs and limits before upload.
            result = engine.submit(root, directory, file_name=args.file, confirm_paid=args.confirm_paid)
        elif args.command == 'status':
            result = engine.status(root, directory)
        elif args.command == 'ingest':
            result = engine.ingest(root, directory)
        else:
            result = engine.reconcile(root, directory, args.file, confirm_remote_absent=args.confirm_remote_absent)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
