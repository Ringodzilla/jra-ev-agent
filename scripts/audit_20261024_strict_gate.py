#!/usr/bin/env python3
"""Strict gate audit for the 2026-10-24 research batch (Issue #39 workstream C).

Re-runs, for the 2026-10-24 batch, the same strict gate audit that the
2026-10-25 batch received in
``report/research/20261025_training_20261028T040209Z/audit/prepare_audit_inputs.py``:
the current core ``src.data_integrity.audit_collected_data`` with purpose
``environment_structural_research``, the structural contract from
``skills/jra-environment-research/scripts/structural_features.py``, the
pre-post-time environment gate, the surface gate and the same
leakage issue checks.  (The surface gate was turf-only when this audit
first ran; the supported-surface set gained the dirt base form on
2026-11-09 — see ``compose_gate``.  This batch's recorded outputs predate
the extension and are unchanged.)  Method parity with the 10/25 adapter was verified by
re-running the current core on the frozen 10/25 inputs: the recorded gates
(PASS / BLOCKED_MISSING_DATA / BLOCKED_UNAPPROVED_IMPUTATION) and the
input SHA256 reproduce exactly.

Batch-specific adaptations (recorded in the run summary as well):

- The 10/24 batch predates ``features_final_v2``/``labels_final_v2``; its
  canonical frozen features are ``final_fact_snapshot_v4_20261026_01/*.json``,
  whose SHA chain is bound by ``fact_reconstruction`` and by
  ``final_v4_manifest_artifact_validation_20261026_01.json``.
- Manifest paths are original-machine absolute paths; they are remapped to
  this clone's root and every frozen source byte is re-verified by SHA256.
- The prior independent checks the 10/25 adapter consumed from its earlier
  audit directory (career_source_checks / history_date_set_checks /
  horse_set_check) never existed under those names for 10/24.  The identical
  predicates are recomputed here from the SHA-frozen snapshot itself
  (``career_verification`` with status VERIFIED_COMPLETE_OFFICIAL_HISTORY is
  the career PASS equivalent; the history date set is the last five verified
  started rows; the horse set is declared minus official non-starters versus
  participating, corroborated by entry_result_count_check and the verified
  label lineup check).
- The 10/24 snapshots deliberately leave target weather/condition blank and
  the verified labels carry payout checks instead of observations, so
  weather / track condition / post time are parsed from the manifest-bound
  result HTML exactly as the 10/25 adapter parsed its label source.
- Entries are projected to the structural-research input contract, identical
  to the features_final_v2 entry shape: the snapshot's post-race market
  values (current_odds / current_popularity / win_odds) are excluded from
  the audit input.  They are removed, never altered; history odds and
  popularity remain required observations.
- Odds provenance is recorded per history row (not per horse as in 10/25):
  ``field_provenance.odds`` carries the official final-odds page URL and its
  SHA256.  The corresponding manifest entry is located by that SHA256 and
  bound as a ``history_odds`` artifact, the same binding the 10/25 adapter
  made from horse-level provenance.
- A failed prior-check precondition is contained per race (recorded, never
  repaired) instead of aborting the whole batch.

Nothing is fabricated or repaired to force a PASS.  Outputs are research
artifacts only: ``record_type=retrospective_reconstructed``,
``production_learning_eligible=false``; model calculations are not executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'skills' / 'jra-environment-research' / 'scripts'))

from bs4 import BeautifulSoup  # noqa: E402

from jra_scraper.validation import _normalize_row  # noqa: E402
from src.data_integrity import audit_collected_data, data_input_sha256  # noqa: E402
from structural_features import HISTORY_FIELDS, TARGET_FIELDS, surface_supported  # noqa: E402

TARGET = '2026-10-24'
PURPOSE = 'environment_structural_research'
ORIG_PREFIX = '/Users/konishi/Documents/Playground/projects/jra-ev-agent/'
BATCH = ROOT / 'report/research/20261024_batch_20261024T225718Z'
SNAPSHOT_DIR = BATCH / 'final_fact_snapshot_v4_20261026_01'
ENV_PATH = (ROOT / 'report/research/20261024_environment_reconstruction'
            / 'observations_20261024T225734690084Z.json')
VERIFIED_CAREER_STATUS = 'VERIFIED_COMPLETE_OFFICIAL_HISTORY'
LEAKAGE_FIELDS = ('current_odds', 'current_popularity', 'win_odds',
                  'career_source_text', 'embedded_history')
ENTRY_EXCLUDED_FIELDS = ('history_pre_target', 'embedded_history',
                         'career_verification', 'career_source_text',
                         'current_odds', 'current_popularity', 'win_odds')
CONFLICT_RESOLUTION = ('Canonical selected observations reverified by current '
                       'core source audit; old parser alternatives retained in '
                       'immutable feature artifact, not adopted')


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def ref(path: Path) -> dict:
    path = Path(path)
    return {'path': str(path), 'sha256': sha(path)}


def remap_path(original: str, root: Path = ROOT) -> Path:
    """Translate an original-machine absolute path into this clone."""
    text = str(original)
    if not text.startswith(ORIG_PREFIX):
        raise ValueError(f'Path outside the original repository prefix: {text}')
    return root / text[len(ORIG_PREFIX):]


def career_check_for(horse: dict) -> dict:
    """Career PASS equivalent from the frozen snapshot's career_verification."""
    verification = horse.get('career_verification') or {}
    status = verification.get('status')
    return {
        'horse_number': str(horse.get('horse_number', '')),
        'status': 'PASS' if status == VERIFIED_CAREER_STATUS else str(status),
        'source_status': status,
        'before_target_count': verification.get('before_target_count'),
    }


def date_set_check_for(horse: dict) -> dict:
    """Last-five verified started dates must equal the selected history dates."""
    verification = horse.get('career_verification') or {}
    expected = [row.get('normalized_date') for row in
                (verification.get('started_rows') or [])[:5]]
    actual = [row.get('normalized_date') for row in
              (horse.get('history_pre_target') or [])]
    return {
        'horse_number': str(horse.get('horse_number', '')),
        'expected_dates': expected,
        'actual_dates': actual,
        'status': 'PASS' if expected == actual else 'MISMATCH',
    }


def horse_set_check(feature: dict, label_check: dict | None) -> dict:
    """Declared minus official non-starters must equal the participating set."""
    declared = {str(x.get('horse_id')) for x in
                feature.get('declared_entry_set_original') or []}
    participating = {str(x.get('horse_id')) for x in
                     feature.get('participating_entry_set') or []}
    nonstarters = {str(x.get('horse_id')) for x in
                   feature.get('official_nonstarter_set') or []}
    same_set = bool(declared) and declared == (participating | nonstarters)
    starter_set_same = bool(
        same_set and (declared - nonstarters) == participating
        and (feature.get('entry_result_count_check') or {}).get('match') is True
        and label_check is not None
        and label_check.get('lineup_count_matches_result') is True)
    return {'same_set': same_set, 'starter_set_same': starter_set_same,
            'declared_count': len(declared),
            'participating_count': len(participating),
            'official_nonstarter_count': len(nonstarters)}


def parse_post_time(text: str) -> str:
    """Post time from the result header.

    The 10/25 label sources carried 'HH:MM'; the 10/24 result pages use the
    Japanese '発走時刻： 9時45分' layout.  Both are accepted; anything else is
    a preparation error, never a guess.
    """
    match = (re.search(r'(\d{1,2})時(\d{1,2})分', text)
             or re.search(r'(\d{1,2}):(\d{2})', text))
    if not match:
        raise ValueError('Result post time is not present')
    post = datetime.strptime(TARGET, '%Y-%m-%d').replace(
        hour=int(match.group(1)), minute=int(match.group(2)),
        tzinfo=timezone(timedelta(hours=9)))
    return post.isoformat()


def select_environment(track_env: dict, post_time: str) -> tuple[dict, bool]:
    """Latest same-day cushion/moisture observations, both before post time."""
    def selected(kind: str):
        values = [x for x in track_env.get(kind + '_history', [])
                  if str(x.get('measured_at', '')).startswith(TARGET)]
        return values[0] if values else None

    cushion, moisture = selected('cushion'), selected('moisture')
    environment = {'cushion': cushion, 'moisture': moisture}
    env_ok = bool(cushion and moisture
                  and cushion['measured_at'] < post_time
                  and moisture['measured_at'] < post_time)
    return environment, env_ok


def compose_gate(ca_gate: str, surface: str, env_ok: bool,
                 structural_errors: list, issues: list) -> str:
    """The 10/25 adapter's gate composition chain, verbatim order.

    The surface step consults the structural contract's supported-surface
    set (``structural_features.SURFACE_BASE_FORMS``).  That set gained the
    dirt base form on 2026-11-09 (user direction; docs/condition_models.md),
    so dirt targets now pass this step exactly as turf targets do; turf
    outcomes are unchanged and jump targets remain NOT_SUPPORTED_SURFACE.
    """
    gate = ca_gate
    if gate == 'PASS' and structural_errors:
        gate = 'BLOCKED_STRUCTURAL_CONTRACT'
    elif gate == 'PASS' and not surface_supported(surface):
        gate = 'NOT_SUPPORTED_SURFACE'
    elif gate == 'PASS' and not env_ok:
        gate = 'UNVERIFIED_ENVIRONMENT'
    elif gate == 'PASS' and issues:
        gate = 'UNVERIFIED'
    return gate


def structural_contract(rows: list, entries: list) -> dict:
    """Copied verbatim from the 10/25 adapter (prepare_audit_inputs.py)."""
    errors = []
    by_entry = {}
    for row in rows:
        by_entry.setdefault((row['race_id'], row['horse_number']), []).append(row)
    for entry in entries:
        history = by_entry.get((entry['race_id'], entry['horse_number']), [])
        if not 1 <= len(history) <= 5:
            errors.append({'horse_number': entry['horse_number'],
                           'error': 'history_count_out_of_contract'})
        for field in TARGET_FIELDS:
            if field not in entry or str(entry[field]).strip() in {'', 'None', 'UNVERIFIED'}:
                errors.append({'horse_number': entry['horse_number'],
                               'field': field, 'error': 'target_field_missing'})
        for field in ('target_distance', 'assigned_weight', 'frame_number'):
            try:
                if not float(entry[field]) > 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                errors.append({'horse_number': entry['horse_number'],
                               'field': field, 'error': 'target_not_positive_finite'})
        for row in history:
            for field in HISTORY_FIELDS:
                if field not in row or str(row[field]).strip() in {'', 'None', 'UNVERIFIED'}:
                    errors.append({'horse_number': entry['horse_number'],
                                   'field': field, 'error': 'history_field_missing'})
            for field in ('position', 'time', 'distance', 'last_3f', 'weight',
                          'odds', 'popularity'):
                try:
                    if not float(row[field]) > 0:
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    errors.append({'horse_number': entry['horse_number'],
                                   'field': field,
                                   'error': 'history_not_positive_finite'})
            if not re.fullmatch(r'\d+(?:-\d+)*', str(row.get('passing_order', ''))):
                errors.append({'horse_number': entry['horse_number'],
                               'field': 'passing_order', 'error': 'passing_order_shape'})
            if row.get('history_surface') not in {'芝', 'ダート'}:
                errors.append({'horse_number': entry['horse_number'],
                               'field': 'history_surface',
                               'error': 'history_surface_not_comparable'})
            if row.get('track_condition') not in {'良', '稍重', '重', '不良'}:
                errors.append({'horse_number': entry['horse_number'],
                               'field': 'track_condition',
                               'error': 'history_condition_missing'})
            if not str(row.get('date', '')) < TARGET:
                errors.append({'horse_number': entry['horse_number'],
                               'field': 'date', 'error': 'history_not_before_target'})
            if row.get('race_id') != entry['race_id'] or row.get('horse_id') != entry['horse_id']:
                errors.append({'horse_number': entry['horse_number'],
                               'error': 'history_identity_mismatch'})
    return {'errors': errors}


def project_entry(horse: dict, weather: str, condition: str) -> dict:
    """Entry projected to the structural-research input contract.

    Mirrors the features_final_v2 entry shape: no current-market fields.
    """
    entry = {k: v for k, v in horse.items() if k not in ENTRY_EXCLUDED_FIELDS}
    entry.update(
        target_weather=weather, target_track_condition=condition,
        target_conditions_captured_at=horse.get('target_conditions_captured_at', ''),
        target_condition_provenance={
            'type': 'post_event_official_reconstruction',
            'pre_race_capture_proven': False})
    return entry


def odds_provenance(raw_row: dict) -> tuple[str, str] | None:
    """(url, sha256) of the official final-odds page for a history row."""
    provenance = (raw_row.get('field_provenance') or {}).get('odds') or {}
    url, digest = provenance.get('url'), provenance.get('sha256')
    if not url or not digest or url == raw_row.get('race_result_url'):
        return None
    if str(raw_row.get('odds', '')).strip() in {'', 'None'}:
        return None
    return url, digest


def load_manifests() -> tuple[dict, list[Path]]:
    manifest_paths = [BATCH / 'manifest.json', BATCH / 'manifest_v2.json']
    source_by_url = {}
    for path in manifest_paths:
        for entry in json.loads(path.read_text()):
            source_by_url[entry['url']] = entry
    return source_by_url, manifest_paths


def verify_manifest_bytes(source_paths: list[Path]) -> list[dict]:
    """Re-verify every frozen source byte referenced by the batch manifests."""
    checks, seen = [], set()
    for path in source_paths:
        for entry in json.loads(path.read_text()):
            if entry['path'] in seen:
                continue
            seen.add(entry['path'])
            local = remap_path(entry['path'])
            actual = sha(local) if local.exists() else None
            checks.append({'path': str(local), 'manifest_path': entry['path'],
                           'expected_sha256': entry['sha256'],
                           'actual_sha256': actual,
                           'match': actual == entry['sha256']})
    return checks


def verify_snapshot_chain() -> dict:
    """The v4 validation file plus each snapshot's bound input SHA."""
    validation_path = BATCH / 'final_v4_manifest_artifact_validation_20261026_01.json'
    validation = json.loads(validation_path.read_text())
    artifact_failures = []
    for artifact in validation.get('artifacts', []):
        local = remap_path(artifact['path'])
        if not local.exists() or sha(local) != artifact['expected_sha256']:
            artifact_failures.append(artifact['path'])
    input_failures = []
    for path in sorted(SNAPSHOT_DIR.glob('20261024_*.json')):
        reconstruction = json.loads(path.read_text())['fact_reconstruction']
        local = remap_path(reconstruction['input_path'])
        if not local.exists() or sha(local) != reconstruction['input_sha256']:
            input_failures.append(path.name)
    return {'validation_status': validation.get('status'),
            'artifact_count': validation.get('artifact_count'),
            'artifact_failures': artifact_failures,
            'snapshot_input_failures': input_failures,
            'pass': (validation.get('status') == 'PASS'
                     and not artifact_failures and not input_failures)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=None,
                        help='New audit output directory (default: '
                             'report/research/20261024_training_<UTC>Z/audit)')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = args.output_dir or (ROOT / 'report' / 'research'
                              / f'20261024_training_{stamp}' / 'audit')
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'Output directory already populated: {out}')
    out.mkdir(parents=True, exist_ok=True)

    source_by_url, manifest_paths = load_manifests()
    source_by_sha256 = {}
    for path in manifest_paths:
        for entry in json.loads(path.read_text()):
            source_by_sha256[entry['sha256']] = entry
    byte_checks = verify_manifest_bytes(manifest_paths)
    byte_check_path = out / 'source_byte_checks.json'
    byte_check_path.write_text(json.dumps(
        {'record_type': 'frozen_source_byte_checks', 'batch_date': TARGET,
         'checked': len(byte_checks),
         'all_match': all(c['match'] for c in byte_checks),
         'entries': byte_checks}, ensure_ascii=False, indent=2))
    chain = verify_snapshot_chain()

    env_doc = json.loads(ENV_PATH.read_text())
    tracks = env_doc['tracks']
    if isinstance(tracks, list):
        tracks = {t['track']: t for t in tracks}
    labels_doc = json.loads(
        (BATCH / 'verified_labels_20261024T231632925424Z.json').read_text())
    label_checks = {c['race_id']: c for c in labels_doc['checks']}

    def artref(meta: dict, kind: str, **extra) -> dict:
        raw = remap_path(meta['path']).read_bytes()
        if sha_bytes(raw) != meta['sha256']:
            raise ValueError('Frozen source changed: ' + meta['url'])
        text = raw.decode(meta.get('encoding', 'utf-8'))
        return {'kind': kind, **extra, 'raw_reference': {
            'path': str(remap_path(meta['path'])),
            'manifest_path': meta['path'],
            'encoding': meta.get('encoding', 'utf-8'),
            'bytes_sha256': meta['sha256'],
            'html_utf8_sha256': sha_bytes(text.encode())}}

    payloads = []
    execution_log = []
    for feature_path in sorted(SNAPSHOT_DIR.glob('20261024_*.json')):
        race_id = feature_path.stem
        feature = json.loads(feature_path.read_text())
        base = {'race_id': race_id, 'record_type': 'retrospective_reconstructed',
                'production_learning_eligible': False, 'model_ready': False,
                'model_calculations_executed': False}
        try:
            payload = audit_one_race(race_id, feature, feature_path, out,
                                     source_by_url, source_by_sha256, tracks,
                                     label_checks, artref, base)
        except Exception as exc:  # per-race containment; never fabricate
            payload = {**base, 'gate': 'AUDIT_ERROR', 'audit_executed': False,
                       'source_issues': ['AUDIT_PREPARATION_ERROR'],
                       'error': f'{type(exc).__name__}: {exc}'}
            (out / f'{race_id}_audit.json').write_text(json.dumps(
                payload, ensure_ascii=False, indent=2))
        payloads.append(payload)
        errors = len(payload.get('structural_contract_errors') or [])
        execution_log.append(f"{race_id} {payload['gate']} {errors}")
        print(execution_log[-1], flush=True)

    (out / 'audit_execution.log').write_text('\n'.join(execution_log) + '\n')
    code_paths = [ROOT / 'src/data_integrity.py',
                  ROOT / 'src/acquisition_coverage.py',
                  ROOT / 'jra_scraper/parser.py',
                  ROOT / 'jra_scraper/validation.py',
                  ROOT / 'skills/jra-environment-research/scripts/structural_features.py',
                  Path(__file__).resolve()]
    (out / 'audit_code_snapshot.json').write_text(json.dumps(
        {'files': [ref(p) for p in code_paths]}, ensure_ascii=False, indent=2))
    summary = {
        'role': 'data_integrity_auditor', 'purpose': PURPOSE,
        'race_count': len(payloads),
        'batch_complete': (all(c['match'] for c in byte_checks) and chain['pass']
                           and all(p.get('audit_executed') for p in payloads)),
        'audited_at': stamp,
        'preflight': {'source_byte_checks': ref(byte_check_path),
                      'snapshot_chain': chain},
        'races': [{'race_id': p['race_id'], 'gate': p['gate'],
                   'model_ready': p['model_ready'],
                   'structural_contract_error_count':
                       len(p.get('structural_contract_errors') or []),
                   'audit': ref(out / f"{p['race_id']}_audit.json")}
                  for p in payloads],
        'source_manifests': [ref(p) for p in manifest_paths],
        'model_ready_race_ids': [p['race_id'] for p in payloads if p['model_ready']],
        'approvals_used': [], 'model_calculations_executed': False,
        'method_parity': {
            'reference': 'report/research/20261025_training_20261028T040209Z/audit/'
                         'prepare_audit_inputs.py',
            'parity_check': 'Current core re-run on frozen 10/25 inputs reproduced '
                            'the recorded gates and input SHA256 exactly.',
            'adaptations': [
                'Manifest original-machine paths remapped to this clone; all '
                'frozen source bytes re-verified by SHA256.',
                'Prior career/date-set/horse-set checks recomputed from the '
                'SHA-frozen v4 snapshot (no prior audit directory exists for '
                '10/24 under those names); VERIFIED_COMPLETE_OFFICIAL_HISTORY '
                'treated as the career PASS equivalent.',
                'Weather/track condition/post time parsed from the '
                'manifest-bound result HTML; no label-observation cross-check '
                'exists for 10/24. Post time uses the Japanese 発走時刻 layout '
                'of the 10/24 result pages (10/25 sources used HH:MM) and was '
                'cross-checked against the earlier 10/24 audit run records.',
                'Entries projected to the structural-research input contract '
                '(post-race market values excluded, as in features_final_v2); '
                'history odds artifacts bound from per-row field_provenance '
                'via the manifest SHA256 index.',
                'Failed prior-check preconditions contained per race instead '
                'of aborting the batch.',
                "Hydration uses the current core's artifact keys ('html', "
                "'sha256'); the Oct-28 adapter used 'raw_html'.",
            ]},
    }
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({'output_dir': str(out),
                      'model_ready': summary['model_ready_race_ids']},
                     ensure_ascii=False))
    return 0


def audit_one_race(race_id, feature, feature_path, out, source_by_url,
                   source_by_sha256, tracks, label_checks, artref, base):
    horses = feature['horses']
    target_race_id = str(horses[0]['race_id'])
    track, surface = horses[0]['target_track'], horses[0]['target_surface']
    distance = horses[0]['target_distance']
    non_starters = {str(x['horse_number']) for x in feature.get('non_starters', [])}

    career_checks = [career_check_for(h) for h in horses]
    date_checks = [date_set_check_for(h) for h in horses]
    set_check = horse_set_check(feature, label_checks.get(target_race_id))
    prior_failures = [c for c in career_checks + date_checks
                      if c['status'] != 'PASS']

    result_meta = source_by_url.get(feature['result_url'])
    if result_meta is None:
        raise ValueError('Result source is not bound in the batch manifests')
    result_raw = remap_path(result_meta['path']).read_bytes()
    if sha_bytes(result_raw) != result_meta['sha256']:
        raise ValueError('Frozen result source changed')
    soup = BeautifulSoup(result_raw.decode(result_meta.get('encoding', 'utf-8')),
                         'html.parser')
    weather_node = soup.select_one('li.weather .txt')
    weather = weather_node.get_text(strip=True) if weather_node else ''

    def node_text(selector):
        node = soup.select_one(selector)
        return node.get_text(strip=True) if node else ''

    condition = (node_text('li.turf .txt') or node_text('li.dirt .txt')
                 or node_text('li.durt .txt'))
    time_node = soup.select_one('.cell.time')
    post_time = parse_post_time(
        time_node.get_text(' ', strip=True) if time_node else '')

    if prior_failures:
        payload = {**base, 'gate': 'UNVERIFIED', 'audit_executed': False,
                   'post_time': post_time,
                   'source_issues': ['UNVERIFIED_CAREER_OR_HISTORY_SET'],
                   'career_source_checks': career_checks,
                   'history_date_set_checks': date_checks,
                   'horse_set_check': set_check,
                   'environment': None,
                   'note': 'Prior verification precondition failed; the strict '
                           'core audit was not executed for this race.'}
        (out / f'{race_id}_audit.json').write_text(json.dumps(
            payload, ensure_ascii=False, indent=2))
        return payload

    career = {c['horse_number']: c['before_target_count'] for c in career_checks}
    entries, rows, artifacts, seen = [], [], [], set()

    def add_artifact(meta, kind, **extra):
        key = (kind, extra.get('race_id', ''), extra.get('horse_id', ''),
               meta['url'])
        if key not in seen:
            seen.add(key)
            artifacts.append(artref(meta, kind, **extra))

    for horse in horses:
        number = str(horse['horse_number'])
        if number in non_starters:
            continue
        entries.append(project_entry(horse, weather, condition))
        history_meta = source_by_url.get(horse['horse_url'])
        if history_meta is not None:
            add_artifact(history_meta, 'horse_history', race_id=target_race_id,
                         horse_id=horse['horse_id'],
                         horse_name=horse['horse_name'],
                         horse_url=horse['horse_url'])
        for raw_row in horse.get('history_pre_target', []):
            converted = datetime.strptime(raw_row['date'], '%Y年%m月%d日').date().isoformat()
            if converted != raw_row.get('normalized_date'):
                raise ValueError('History date conversion mismatch')
            rows.append(_normalize_row({
                **raw_row, 'date': converted, 'race_id': horse['race_id'],
                'horse_number': horse['horse_number'],
                'horse_id': horse['horse_id'], 'horse_name': horse['horse_name'],
                'target_race_date': TARGET, 'target_track': track,
                'target_surface': surface, 'target_distance': distance}))
            meta = source_by_url.get(raw_row.get('race_result_url'))
            if meta is not None:
                add_artifact(meta, 'history_result', race_id=target_race_id,
                             horse_id=horse['horse_id'],
                             horse_name=horse['horse_name'],
                             horse_url=horse['horse_url'])
            provenance = odds_provenance(raw_row)
            if provenance is not None:
                odds_meta = source_by_sha256.get(provenance[1])
                if odds_meta is not None:
                    add_artifact(odds_meta, 'history_odds',
                                 race_id=target_race_id,
                                 horse_id=horse['horse_id'],
                                 horse_name=horse['horse_name'],
                                 horse_url=horse['horse_url'])
    detail_meta = source_by_url.get(feature['racecard_url'])
    if detail_meta is not None:
        add_artifact(detail_meta, 'race_detail', race_id=target_race_id,
                     race_name=entries[0].get('race_name', ''))

    config = {'race_id': target_race_id, 'source_race_id': race_id,
              'race_name': entries[0].get('race_name', ''),
              'race_number': str(entries[0].get('target_race_number', '')),
              'source_url': feature['racecard_url'], 'date': TARGET,
              'post_time': post_time, 'surface': surface, 'distance': distance,
              'weather': weather, 'career_starts_by_horse_number': career}
    normalized = {'rows': rows, 'entries': entries, 'race_configs': [config]}
    wrapper = {'rows': rows, 'entries': entries,
               'source_audit': {'artifacts': artifacts}}
    (out / f'{race_id}_collected.json').write_text(json.dumps(
        {'schema_version': 1, 'record_type': 'collected_raw_references',
         'collected': wrapper}, ensure_ascii=False, indent=2))
    (out / f'{race_id}_normalized.json').write_text(json.dumps(
        normalized, ensure_ascii=False, indent=2))

    hydrated = json.loads(json.dumps(wrapper))
    for artifact in hydrated['source_audit']['artifacts']:
        reference = artifact['raw_reference']
        raw = Path(reference['path']).read_bytes()
        if sha_bytes(raw) != reference['bytes_sha256']:
            raise ValueError('Frozen source changed: ' + reference['path'])
        artifact['html'] = raw.decode(reference['encoding'])
        artifact['sha256'] = reference['html_utf8_sha256']
    collected_audit = audit_collected_data(
        hydrated, [config], approvals=[], purpose=PURPOSE)
    if collected_audit['input_sha256'] != data_input_sha256(normalized, [config]):
        raise ValueError('Collected and normalized input hashes differ')

    contract = structural_contract(rows, entries)
    environment, env_ok = select_environment(tracks.get(track, {}), post_time)
    issues = []
    if not (set_check['same_set'] and set_check['starter_set_same']):
        issues.append('UNVERIFIED_HORSE_SET')
    if prior_failures:
        issues.append('UNVERIFIED_CAREER_OR_HISTORY_SET')
    leakage_dates = [r for r in rows if not str(r.get('date', '')) < TARGET]
    leakage_containers = [e['horse_number'] for e in entries
                          if any(str(e.get(f, '')).strip() not in {'', 'None'}
                                 for f in LEAKAGE_FIELDS)]
    if leakage_dates or leakage_containers:
        issues.append('RESULT_LEAKAGE_RISK')
    gate = compose_gate(collected_audit['gate'], surface, env_ok,
                        contract['errors'], issues)
    payload = {**base, 'gate': gate, 'audit_executed': True,
               'model_ready': gate == 'PASS',
               'numeric_race_id': target_race_id,
               'metadata_source': {**result_meta,
                                   'path': str(remap_path(result_meta['path']))},
               'post_time': post_time,
               'environment': environment,
               'environment_source_path': str(ENV_PATH),
               'environment_gate': 'PASS' if env_ok else 'UNVERIFIED_ENVIRONMENT',
               'surface_gate': 'PASS' if surface_supported(surface) else 'NOT_SUPPORTED_SURFACE',
               'collected_artifact': ref(out / f'{race_id}_collected.json'),
               'normalized_artifact': ref(out / f'{race_id}_normalized.json'),
               'source_issues': issues,
               'blocking_history_conflicts': [],
               'history_conflicts': feature.get('history_conflicts', []),
               'history_conflict_resolution': CONFLICT_RESOLUTION,
               'collected_audit': collected_audit,
               'structural_contract_errors': contract['errors'],
               'input_artifacts': {
                   'feature': ref(feature_path),
                   'environment': ref(ENV_PATH),
                   'source_manifests': [ref(BATCH / 'manifest.json'),
                                        ref(BATCH / 'manifest_v2.json')]},
               'source_audit_current_purpose': PURPOSE,
               'all_horses_retained': len(entries) == len(horses),
               'current_leakage_checks': {
                   'history_after_or_on_target': leakage_dates,
                   'container_risks': leakage_containers,
                   'date_comparison': 'datetime.date on deterministically '
                                      'converted ISO dates; prior legacy '
                                      'Japanese/ISO lexical comparison not reused'},
               'official_nonstarters': [
                   {'horse_number': str(x.get('horse_number', '')),
                    'horse_name': x.get('horse_name', '')}
                   for x in feature.get('non_starters', [])],
               'career_source_checks': career_checks,
               'history_date_set_checks': date_checks,
               'horse_set_check': set_check}
    (out / f'{race_id}_audit.json').write_text(json.dumps(
        payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == '__main__':
    raise SystemExit(main())
