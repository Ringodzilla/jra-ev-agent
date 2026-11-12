"""Run the Issue #40 funnel confirmation layer (validation stage).

Modes:

- ``--freeze-ap1``: record the AP-1 shortlist freeze.  The shortlist in
  ``shortlist.json`` must agree with ``screening_results.json`` and the
  research plan must match the frozen plan SHA; nothing is computed.
- ``--verify-block``: metadata-only collation of the validation block
  against the handover block manifest (counts and the block SHA-256).
  No race artifact, outcome or label is opened.
- ``--freeze-code``: freeze this confirmation code (SHA-256 of the
  three confirmation files) after its synthetic tests pass.
- ``--run-validation``: the one-shot validation evaluation.  Requires
  the AP-1 freeze and the code freeze, re-verifies the plan, the
  screening code SHAs, the pool manifest and the block hash, then
  evaluates the eight frozen hypotheses on the validation block exactly
  once.  A second invocation is refused.  The test block is never
  touched: this runner holds no code path that can load it
  (``funnel_confirmation`` refuses every split but ``validation``).

Research track only; outputs carry ``production_eligible=false``.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import condition_fit as cf  # noqa: E402
import funnel_confirmation as fc  # noqa: E402
import funnel_data as fd  # noqa: E402
import funnel_registry as fr  # noqa: E402
import run_funnel_screening as rfs  # noqa: E402

RUN_DIR = rfs.RUN_DIR
WORKSPACE = ROOT.parent
CONFIRMATION_MANIFEST_PATH = rfs.CONFIRMATION_MANIFEST_PATH
EXPECTED_PLAN_SHA256 = ('656caa59e6ef78f177f4d38fb823e1520d1fad56cd5a610706'
                        '38f335fe90da57')
CONFIRMATION_CODE_FILES = {
    'funnel_confirmation.py': 'skills/jra-environment-research/scripts/'
                              'funnel_confirmation.py',
    'run_funnel_confirmation.py': 'skills/jra-environment-research/'
                                  'scripts/run_funnel_confirmation.py',
    'test_funnel_confirmation.py': 'skills/jra-environment-research/'
                                   'scripts/test_funnel_confirmation.py',
}
SHORTLIST_IDS = ['CAL-08', 'CAL-06', 'CAL-02', 'UP-02', 'SEG-08',
                 'UP-04', 'SEG-02', 'UP-06']
AP1_BASIS = ('User approval in the main conversation on 2026-11-12: '
             '"この8件で確認層へ進める" (proceed to the confirmation '
             'layer with these eight).')


def _jsonable(value):
    import numpy as np
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    return value


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False,
                               indent=2) + '\n', encoding='utf-8')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _log(entry):
    path = RUN_DIR / 'confirmation_log.json'
    entries = []
    if path.exists():
        entries = json.loads(path.read_text(encoding='utf-8'))
    entries.append(entry)
    _write(path, entries)


def _load_plan():
    plan_path = RUN_DIR / 'research_plan.json'
    digest = cf.sha256(plan_path)
    if digest != EXPECTED_PLAN_SHA256:
        raise SystemExit('Research plan SHA mismatch: ' + digest)
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if plan['approval']['status'] != 'approved':
        raise SystemExit('AP-0 not approved in the frozen plan')
    for name, rel in rfs.CODE_FILES.items():
        actual = cf.sha256(ROOT / rel)
        if actual != plan['code_sha256'][name]:
            raise SystemExit(f'Screening code changed since freeze: '
                             f'{name}')
    return plan


def _shortlist():
    frozen = json.loads((RUN_DIR / 'shortlist.json').read_text(
        encoding='utf-8'))
    results = json.loads((RUN_DIR / 'screening_results.json').read_text(
        encoding='utf-8'))
    if frozen != results['shortlist']:
        raise SystemExit('shortlist.json disagrees with '
                         'screening_results.json')
    entries = frozen['shortlist']
    if [e['id'] for e in entries] != SHORTLIST_IDS and \
            sorted(e['id'] for e in entries) != sorted(SHORTLIST_IDS):
        raise SystemExit('Shortlist membership differs from the AP-1 '
                         'presentation')
    return entries, results


def freeze_ap1() -> dict:
    plan = _load_plan()
    entries, _ = _shortlist()
    path = RUN_DIR / 'ap1_freeze.json'
    if path.exists():
        raise SystemExit('AP-1 freeze already recorded')
    record = {
        'point': 'AP-1',
        'status': 'frozen',
        'frozen_at': _now(),
        'experiment_id': plan['experiment_id'],
        'plan_sha256': EXPECTED_PLAN_SHA256,
        'screening_code_sha256': plan['code_sha256'],
        'pool_manifest_sha256': plan['pool']['manifest_sha256'],
        'shortlist': entries,
        'n_screened': 60,
        'k_shortlist': len(entries),
        'basis': AP1_BASIS,
        'note': 'Frozen: hypothesis definitions, selected grids and '
                'gate wording are unchanged from the screening run. '
                'Confirmation evaluates the shortlist against the base '
                'form (protocol section 8).',
        'production_eligible': False,
    }
    _write(path, record)
    _log({'event': 'ap1_freeze', 'at': record['frozen_at'],
          'shortlist': [e['id'] for e in entries]})
    return record


def verify_block() -> dict:
    payload = json.loads(CONFIRMATION_MANIFEST_PATH.read_text(
        encoding='utf-8'))
    block = {b['split']: b for b in payload['blocks']}['validation']
    config = fd.SOURCES[fc.EXPANSION_SOURCE]
    summary_path = ROOT / config['summary_path']
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    entries = fc.build_block_entries(summary, 'validation')
    digest = fc.block_list_sha256(entries)
    by_surface = {s: sum(1 for e in entries if e['surface'] == s)
                  for s in ('芝', 'ダート')}
    result = {
        'split': 'validation',
        'race_count': len(entries),
        'by_surface': by_surface,
        'computed_block_sha256': digest,
        'expected_block_sha256': block['block_manifest_sha256'],
        'match': digest == block['block_manifest_sha256']
                 and len(entries) == block['race_count']
                 and by_surface == block['by_surface'],
        'metadata_only': True,
    }
    _log({'event': 'validation_block_metadata_collation', 'at': _now(),
          'match': result['match'], 'race_count': len(entries)})
    if not result['match']:
        raise SystemExit('Validation block collation FAILED: '
                         + json.dumps(result, ensure_ascii=False))
    return result


def freeze_code() -> dict:
    path = RUN_DIR / 'confirmation_code_freeze.json'
    if path.exists():
        raise SystemExit('Confirmation code freeze already recorded')
    record = {
        'frozen_at': _now(),
        'files': {name: cf.sha256(ROOT / rel)
                  for name, rel in CONFIRMATION_CODE_FILES.items()},
        'note': 'Frozen after synthetic tests passed (see '
                'test_funnel_confirmation.py).  The validation run '
                'refuses if any of these files changes.',
        'production_eligible': False,
    }
    _write(path, record)
    _log({'event': 'confirmation_code_freeze', 'at': record['frozen_at'],
          'files': record['files']})
    return record


def _check_code_freeze():
    path = RUN_DIR / 'confirmation_code_freeze.json'
    if not path.exists():
        raise SystemExit('Confirmation code is not frozen yet')
    record = json.loads(path.read_text(encoding='utf-8'))
    for name, rel in CONFIRMATION_CODE_FILES.items():
        if cf.sha256(ROOT / rel) != record['files'][name]:
            raise SystemExit(f'Confirmation code changed: {name}')
    return record


def _fold_improvements_vs_base(results, spec):
    records = {r['id']: r for r in results['hypotheses']}
    base_folds = None
    for record in results['hypotheses']:
        if record.get('control') == 'base_form' \
                and record.get('surface') == spec['surface'] \
                and record.get('folds'):
            folds = [f['control']['log_loss'] for f in record['folds']]
            if base_folds is not None and folds != base_folds:
                raise SystemExit('Base-form fold metrics disagree '
                                 'across records')
            base_folds = folds
    if base_folds is None:
        raise SystemExit('No base-form fold record for surface '
                         + spec['surface'])
    candidate = records[spec['id']]
    return [b - f['candidate']['log_loss']
            for b, f in zip(base_folds, candidate['folds'])]


def run_validation() -> dict:
    freeze_path = RUN_DIR / 'ap1_freeze.json'
    if not freeze_path.exists():
        raise SystemExit('AP-1 freeze is missing')
    freeze = json.loads(freeze_path.read_text(encoding='utf-8'))
    _check_code_freeze()
    metrics_path = RUN_DIR / 'confirmation_metrics.json'
    if metrics_path.exists():
        raise SystemExit('confirmation_metrics.json already exists: '
                         'the validation block may be evaluated only '
                         'once')
    plan = _load_plan()
    entries, results = _shortlist()
    collation = verify_block()
    manifest = json.loads(Path(plan['pool']['manifest_path']).read_text(
        encoding='utf-8'))
    if manifest['manifest_sha256'] != plan['pool']['manifest_sha256']:
        raise SystemExit('Screening manifest does not match the plan')
    fd.verify_manifest(manifest)
    races = fd.load_screening_races(manifest, rfs.allowed_roots())
    train_races = [r for r in races if r['pool_block'] == 'train']
    config = fd.SOURCES[fc.EXPANSION_SOURCE]
    sources = {s['source_id']: s for s in manifest['sources']}
    block_entries = fc.build_block_entries(json.loads(
        (ROOT / config['summary_path']).read_text(encoding='utf-8')),
        'validation')
    eval_races = fc.load_confirmation_races(
        block_entries, ROOT / config['summary_path'],
        sources[fc.EXPANSION_SOURCE]['summary_sha256'],
        ROOT / config['dataset_dir'],
        rfs.allowed_roots()[1], collation['expected_block_sha256'])
    records = []
    for entry in freeze['shortlist']:
        spec = fr.BY_ID[entry['id']]
        folds = _fold_improvements_vs_base(results, spec)
        records.append(fc.evaluate_confirmation(
            train_races, eval_races, spec, entry['selected_grid'],
            folds))
    payload = {
        'schema': 'funnel_confirmation_metrics_v1',
        'experiment_id': plan['experiment_id'],
        'stage': 'validation',
        'executed_at': _now(),
        'plan_sha256': EXPECTED_PLAN_SHA256,
        'ap1_freeze': 'ap1_freeze.json',
        'confirmation_code_freeze': 'confirmation_code_freeze.json',
        'block_collation': collation,
        'n_screened': results['n_hypotheses'],
        'k_shortlist': len(records),
        'hypotheses': records,
        'test_block': 'unopened (AP-2 is a separate user decision)',
        'production_eligible': False,
    }
    _write(metrics_path, payload)
    _log({'event': 'validation_run', 'at': payload['executed_at'],
          'hypotheses': [r['id'] for r in records],
          'verdicts': {r['id']: r['gate_validation_side']['verdict']
                       for r in records}})
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-ap1', action='store_true')
    parser.add_argument('--verify-block', action='store_true')
    parser.add_argument('--freeze-code', action='store_true')
    parser.add_argument('--run-validation', action='store_true')
    args = parser.parse_args(argv)
    if args.freeze_ap1:
        record = freeze_ap1()
        print(json.dumps({'ap1_frozen': True,
                          'shortlist': [e['id'] for e in
                                        record['shortlist']]},
                         ensure_ascii=False))
    elif args.verify_block:
        print(json.dumps(verify_block(), ensure_ascii=False))
    elif args.freeze_code:
        record = freeze_code()
        print(json.dumps({'code_frozen': True, 'files': record['files']},
                         ensure_ascii=False))
    elif args.run_validation:
        payload = run_validation()
        print(json.dumps(
            {'stage': 'validation',
             'verdicts': {r['id']: r['gate_validation_side']['verdict']
                          for r in payload['hypotheses']}},
            ensure_ascii=False))
    else:
        parser.error('one mode is required')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
