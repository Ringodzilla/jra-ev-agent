#!/usr/bin/env python3
"""Strict gate audit for the migrated 2026-05-02..2026-09-27 research dataset.

Issue #39, workstream C expansion.  Applies the same strict gate audit that
workstream C ran for the 2026-10-24 batch (and that the 2026-10-25 batch
received before it) to the 298 learning candidates of the research archive
published at ``research/datasets/jra-20260502-20260927`` (jra-ev-agent main,
commit f5ac2b1): the current core ``src.data_integrity.audit_collected_data``
with purpose ``environment_structural_research`` and no approvals, the
structural contract from
``skills/jra-environment-research/scripts/structural_features.py``, the
pre-post-time environment gate, the turf-only surface gate and the same
leakage issue checks.  Gate composition reuses workstream C's
``compose_gate`` verbatim.

Candidate selection rule: a race is a candidate exactly when its
``audit_preparation`` record has ``state == 'PASS'`` (298 races; the split —
train 181 / validation 58 / test 59 — and the surface/venue/distance are
joined from ``races.jsonl.gz``).  Every other race was already excluded
upstream and is not audited here.

Documented adaptations relative to the 10/24 driver (recorded in the run
summary as well):

- Before anything is audited, the archive is verified against its own
  ``manifest.json`` (per-file bytes, SHA256 and record counts) and the
  dataset's ``verify_dataset.py`` must report PASS; the run aborts
  otherwise.  Nothing is audited on unverified bytes.
- Entries are projected with workstream C's ``project_entry`` (post-race
  market values excluded, never altered).  The archive's feature records
  contain no target-market fields by construction (the dataset verifier
  rejects them); the projection still enforces the exclusion.  The archive
  stores ``horse_number``/``frame_number`` as integers where the v4
  snapshots stored strings, so the projection coerces them to ``str`` —
  the structural contract matches identity tuples without coercion.
- History rows pass through the same ``_normalize_row`` as workstream C.
- The prior career/date-set checks reuse workstream C's functions verbatim
  (the archive's ``career_verification`` has the identical shape).  The
  horse-set check is recomputed from cross-source agreement — the feature
  record's start statuses versus the audit-preparation counts and official
  non-starters versus the ``races.jsonl.gz`` counts — because the archive
  separates labels into outcomes files, which this driver never opens.
- The structural contract is the 10/25/10/24 adapter's, copied with the
  batch-global ``TARGET`` date replaced by each race's own target date
  (the original closes over a module constant for its single batch date;
  importing it unchanged would silently test every history row against
  2026-10-24).  Every other predicate is verbatim.
- Environment selection mirrors workstream C's semantics — same-day
  cushion and surface moisture observations, both measured before post
  time — applied to the archive's per-venue-day environment record, with
  the race's own date as the day key.  Post time is constructed strictly
  from ``race_info`` (date + HH:MM, +09:00); anything malformed is a
  per-race preparation error, never a guess.
- Source verification: the archive deliberately excludes source document
  bodies (``source_document_bodies_included`` is false in its manifest),
  so the core's hash-bound reparse cannot verify any row and
  ``source_audit.artifacts`` is empty by construction; every race's
  reparse status is UNVERIFIED, exactly as the core reports it.  In place
  of reparse, provenance is bound by SHA: every source descriptor the
  projection relies on (race entry, per-horse career/history, per-row
  result/odds sources, environment sources) is checked for
  (url, sha256) membership in the manifest-verified ``source_index`` and
  the binding outcome is recorded per race.  The binding evidences the
  archive's internal consistency only; it never changes the core gate.
- Outcomes files (labels: results, payouts, final odds) are never opened
  by this driver.  ``FORBIDDEN_OUTCOME_KEYS`` names their label-only
  top-level keys; the projection is tested to keep them out of the inputs.

Nothing is fabricated or repaired to force a PASS.  Outputs are research
artifacts only: ``record_type=retrospective_reconstructed``,
``production_learning_eligible=false``; admitted races stay
``posthoc_exploratory_training_only`` with ``production_eligible=false``
and never count toward the production minimums.  Model calculations are
not executed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import re
import sys
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills' / 'jra-environment-research' / 'scripts'))

from jra_scraper.validation import _normalize_row  # noqa: E402
from src.data_integrity import audit_collected_data, data_input_sha256  # noqa: E402
from structural_features import HISTORY_FIELDS, TARGET_FIELDS  # noqa: E402

from audit_20261024_strict_gate import (  # noqa: E402
    career_check_for,
    compose_gate,
    date_set_check_for,
    project_entry,
    ref,
)

PURPOSE = 'environment_structural_research'
DATASET_DIR = ROOT / 'research' / 'datasets' / 'jra-20260502-20260927'
DATASET_SOURCE = {'repo': 'Ringodzilla/jra-ev-agent',
                  'commit': 'f5ac2b1b3b58333815cc95e3e4f3d1285a532d5c',
                  'path': 'research/datasets/jra-20260502-20260927'}
VERIFIED_CAREER_STATUS = 'VERIFIED_COMPLETE_OFFICIAL_HISTORY'
# Label-only top-level keys of the outcomes records.  They must never
# appear in audit inputs; the dataset verifier rejects them in features
# for the same reason.
FORBIDDEN_OUTCOME_KEYS = ('observations', 'final_odds', 'payouts_factual')
LEAKAGE_FIELDS = ('current_odds', 'current_popularity', 'win_odds',
                  'career_source_text', 'embedded_history')
ENTRY_EXCLUDED_EXTRA = ('target_start_status', 'target_start_status_source',
                        'career_source', 'career_starts', 'history_count',
                        'course_fact_source')
MONTHS = ('05', '06', '07', '08', '09')
JST = timezone(timedelta(hours=9))


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_jsonl_gz(path: Path) -> list:
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def distance_band(surface: str, distance) -> str:
    """Rollup band for reporting only; exact-distance cells stay primary."""
    if surface == '障害':
        return '障害'
    metres = int(distance)
    if metres <= 1400:
        return '短距離（〜1400m）'
    if metres <= 1800:
        return 'マイル（1500–1800m）'
    if metres <= 2200:
        return '中距離（1900–2200m）'
    return '長距離（2300m〜）'


def verify_archive_manifest(dataset_dir: Path) -> dict:
    """Per-file bytes/SHA256/record-count check against manifest.json."""
    manifest = json.loads((dataset_dir / 'manifest.json').read_text())
    files = []
    for item in manifest['files']:
        path = dataset_dir / item['file']
        raw = path.read_bytes()
        records = 0
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            for line in handle:
                records += bool(line.strip())
        files.append({'file': item['file'],
                      'bytes_match': len(raw) == item['bytes'],
                      'sha256_match': sha_bytes(raw) == item['sha256'],
                      'records_match': records == item['records']})
    return {'manifest': ref(dataset_dir / 'manifest.json'),
            'collection_sha256': manifest.get('collection_sha256'),
            'source_document_bodies_included':
                manifest.get('source_document_bodies_included'),
            'files': files,
            'all_match': all(f['bytes_match'] and f['sha256_match']
                             and f['records_match'] for f in files)}


def run_dataset_verifier(dataset_dir: Path) -> dict:
    """Execute the archive's own verify_dataset.py and capture its verdict."""
    spec = importlib.util.spec_from_file_location(
        'verify_dataset', dataset_dir / 'verify_dataset.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        module.verify()
    verdict = json.loads(buffer.getvalue().strip().splitlines()[-1])
    return {'script': ref(dataset_dir / 'verify_dataset.py'), **verdict}


def select_candidates(preparations: dict, races: dict) -> list:
    """The candidate rule: audit_preparation state PASS, joined with races."""
    candidates = []
    for race_id, prep in preparations.items():
        if prep.get('state') != 'PASS':
            continue
        race = races[race_id]
        candidates.append({'race_id': race_id, 'split': race['split'],
                           'surface': race['surface'], 'track': race['track'],
                           'distance': int(race['distance_m']),
                           'date': race['date']})
    return sorted(candidates, key=lambda c: c['race_id'])


def project_entry_dataset(horse: dict, weather: str, condition: str) -> dict:
    """Workstream C's projection plus archive-specific exclusions/coercions."""
    entry = project_entry(horse, weather, condition)
    for field in ENTRY_EXCLUDED_EXTRA:
        entry.pop(field, None)
    for field in FORBIDDEN_OUTCOME_KEYS:
        entry.pop(field, None)
    entry['horse_number'] = str(entry['horse_number'])
    entry['frame_number'] = str(entry['frame_number'])
    return entry


def horse_set_check_dataset(feature: dict, prep: dict, race_row: dict) -> dict:
    """Declared minus official non-starters must equal the participating set.

    Cross-source form of workstream C's check: the feature record's start
    statuses define the sets; the audit-preparation counts and official
    non-starter identities and the races.jsonl.gz counts must all agree.
    """
    declared = {str(h['horse_id']) for h in feature.get('horses') or []}
    participating = {str(h['horse_id']) for h in feature.get('horses') or []
                     if h.get('target_start_status') == 'STARTER'}
    nonstarters = declared - participating
    prep_nonstarters = {str(x['horse_id']) for x in
                        prep.get('official_nonstarters') or []}
    same_set = bool(declared) and (
        len(declared) == prep.get('declared_field_count') == race_row.get('declared')
        and len(participating) == prep.get('starting_field_count')
        == race_row.get('starters')
        and prep_nonstarters == nonstarters)
    starter_set_same = bool(same_set and (declared - nonstarters) == participating)
    return {'same_set': same_set, 'starter_set_same': starter_set_same,
            'declared_count': len(declared),
            'participating_count': len(participating),
            'official_nonstarter_count': len(nonstarters)}


def parse_post_time(race_date: str, hhmm: str) -> str:
    """Strict post time from race_info; malformed input is an error."""
    if not re.fullmatch(r'\d{2}:\d{2}', str(hhmm or '')):
        raise ValueError(f'Race post time is not HH:MM: {hhmm!r}')
    post = datetime.strptime(f'{race_date} {hhmm}', '%Y-%m-%d %H:%M')
    return post.replace(tzinfo=JST).isoformat()


def select_environment_dataset(env: dict, surface: str, race_date: str,
                               post_time: str) -> tuple[dict, bool]:
    """Same-day cushion/moisture observations, both before post time.

    Mirrors workstream C's ``select_environment`` semantics over the
    archive's single per-venue-day record instead of history lists.
    """
    cushion = None
    if (env.get('cushion') is not None
            and str(env.get('cushion_measured_at', '')).startswith(race_date)):
        cushion = {'measured_at': env['cushion_measured_at'],
                   'cushion': env['cushion']}
    moisture = None
    if str(env.get('moisture_measured_at', '')).startswith(race_date):
        moisture = {'measured_at': env['moisture_measured_at'],
                    'turf_moisture_goal': env.get('turf_moisture_goal'),
                    'turf_moisture_corner4': env.get('turf_moisture_corner4'),
                    'dirt_moisture_goal': env.get('dirt_moisture_goal'),
                    'dirt_moisture_corner4': env.get('dirt_moisture_corner4')}
    keys = {'芝': ('turf_moisture_goal', 'turf_moisture_corner4'),
            'ダート': ('dirt_moisture_goal', 'dirt_moisture_corner4')}.get(surface, ())
    moisture_ok = bool(moisture and keys
                       and any(moisture.get(k) is not None for k in keys))
    env_ok = bool(cushion and moisture_ok
                  and cushion['measured_at'] < post_time
                  and moisture['measured_at'] < post_time)
    return {'cushion': cushion, 'moisture': moisture}, env_ok


def structural_contract_dataset(rows: list, entries: list,
                                target_date: str) -> dict:
    """The 10/25/10/24 structural contract with a per-race target date.

    Copied from the adapters; the only change is that the history-date
    predicate compares against ``target_date`` (the adapters close over
    their batch's single TARGET constant).
    """
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
            if not str(row.get('date', '')) < target_date:
                errors.append({'horse_number': entry['horse_number'],
                               'field': 'date', 'error': 'history_not_before_target'})
            if row.get('race_id') != entry['race_id'] or row.get('horse_id') != entry['horse_id']:
                errors.append({'horse_number': entry['horse_number'],
                               'error': 'history_identity_mismatch'})
    return {'errors': errors}


def collect_source_bindings(feature: dict) -> list:
    """Source descriptors the projection relies on, deduplicated."""
    seen, bindings = set(), []

    def add(kind, descriptor):
        if not isinstance(descriptor, dict):
            return
        url, digest = descriptor.get('url'), descriptor.get('sha256')
        if not url or not digest or (kind, url, digest) in seen:
            return
        seen.add((kind, url, digest))
        bindings.append({'kind': kind, 'url': url, 'sha256': digest})

    add('race_detail', feature.get('target_entry_source'))
    for source in (feature.get('environment', {}).get('sources') or {}).values():
        add('environment', source)
    for horse in feature.get('horses') or []:
        add('horse_history', horse.get('career_source'))
        for row in horse.get('history_pre_target') or []:
            add('history_result', row.get('official_result_source'))
            for odds_source in row.get('official_odds_sources') or []:
                add('history_odds', odds_source)
    return bindings


def check_bindings(bindings: list, index_set: set) -> dict:
    unbound = [b for b in bindings if (b['url'], b['sha256']) not in index_set]
    return {'descriptors': len(bindings), 'bound': len(bindings) - len(unbound),
            'unbound': len(unbound), 'unbound_examples': unbound[:5]}


def find_forbidden_keys(node, path='') -> list:
    """Recursive scan for label-only outcome keys inside projected inputs."""
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_OUTCOME_KEYS:
                hits.append(f'{path}/{key}')
            hits.extend(find_forbidden_keys(value, f'{path}/{key}'))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(find_forbidden_keys(value, f'{path}[{index}]'))
    return hits


def project_race_inputs(feature: dict, info: dict) -> tuple[list, list]:
    """Project one archive feature record to (entries, normalized rows)."""
    track = info['track']
    surface = info['target_surface']
    distance = info['target_distance']
    target_date = info['race_date']
    entries, rows = [], []
    for horse in feature.get('horses') or []:
        if horse.get('target_start_status') != 'STARTER':
            continue
        entries.append(project_entry_dataset(
            horse, info.get('weather', ''), info.get('track_condition', '')))
        for raw_row in horse.get('history_pre_target') or []:
            rows.append(_normalize_row({
                **raw_row, 'race_id': horse['race_id'],
                'horse_number': horse['horse_number'],
                'horse_id': horse['horse_id'], 'horse_name': horse['horse_name'],
                'target_race_date': target_date, 'target_track': track,
                'target_surface': surface, 'target_distance': distance}))
    return entries, rows


def audit_one_race(race_id, feature, prep, race_row, out, index_set, base,
                   feature_ref):
    info = feature['race_info']
    horses = feature['horses']
    track = info['track']
    surface = info['target_surface']
    distance = info['target_distance']
    target_date = info['race_date']
    post_time = parse_post_time(target_date, info.get('post_time', ''))

    career_checks = [career_check_for(h) for h in horses]
    date_checks = [date_set_check_for(h) for h in horses]
    set_check = horse_set_check_dataset(feature, prep, race_row)
    prior_failures = [c for c in career_checks + date_checks
                      if c['status'] != 'PASS']
    bindings = check_bindings(collect_source_bindings(feature), index_set)
    segment = {'surface': surface, 'venue': track, 'distance': int(distance),
               'band': distance_band(surface, distance)}

    if prior_failures:
        payload = {**base, 'gate': 'UNVERIFIED', 'audit_executed': False,
                   'post_time': post_time, 'split': race_row['split'],
                   'segment': segment,
                   'source_issues': ['UNVERIFIED_CAREER_OR_HISTORY_SET'],
                   'career_source_checks': career_checks,
                   'history_date_set_checks': date_checks,
                   'horse_set_check': set_check,
                   'source_bindings': bindings,
                   'environment': None}
        (out / f'{race_id}_audit.json').write_text(json.dumps(
            payload, ensure_ascii=False, indent=2))
        return payload

    career = {c['horse_number']: c['before_target_count'] for c in career_checks}
    entries, rows = project_race_inputs(feature, info)
    config = {'race_id': race_id, 'source_race_id': race_id,
              'race_name': info.get('race_name', ''),
              'race_number': str(info.get('race_number', '')),
              'source_url': race_row.get('source_url', ''), 'date': target_date,
              'post_time': post_time, 'surface': surface, 'distance': distance,
              'weather': info.get('weather', ''),
              'career_starts_by_horse_number': career}
    normalized = {'rows': rows, 'entries': entries, 'race_configs': [config]}
    wrapper = {'rows': rows, 'entries': entries,
               'source_audit': {'artifacts': []}}
    (out / f'{race_id}_collected.json').write_text(json.dumps(
        {'schema_version': 1, 'record_type': 'collected_raw_references',
         'collected': wrapper}, ensure_ascii=False, indent=2))
    (out / f'{race_id}_normalized.json').write_text(json.dumps(
        normalized, ensure_ascii=False, indent=2))

    collected_audit = audit_collected_data(
        wrapper, [config], approvals=[], purpose=PURPOSE)
    if collected_audit['input_sha256'] != data_input_sha256(normalized, [config]):
        raise ValueError('Collected and normalized input hashes differ')

    contract = structural_contract_dataset(rows, entries, target_date)
    environment, env_ok = select_environment_dataset(
        feature.get('environment') or {}, surface, target_date, post_time)
    issues = []
    if not (set_check['same_set'] and set_check['starter_set_same']):
        issues.append('UNVERIFIED_HORSE_SET')
    if bindings['unbound']:
        issues.append('SOURCE_INDEX_BINDING_INCOMPLETE')
    leakage_dates = [r for r in rows if not str(r.get('date', '')) < target_date]
    leakage_containers = [e['horse_number'] for e in entries
                          if any(str(e.get(f, '')).strip() not in {'', 'None'}
                                 for f in LEAKAGE_FIELDS)]
    forbidden = find_forbidden_keys({'rows': rows, 'entries': entries})
    if leakage_dates or leakage_containers or forbidden:
        issues.append('RESULT_LEAKAGE_RISK')
    gate = compose_gate(collected_audit['gate'], surface, env_ok,
                        contract['errors'], issues)
    payload = {**base, 'gate': gate, 'audit_executed': True,
               'model_ready': gate == 'PASS',
               'pool_labels': {'usage': 'posthoc_exploratory_training_only',
                               'production_eligible': False}
               if gate == 'PASS' else None,
               'split': race_row['split'], 'segment': segment,
               'post_time': post_time,
               'environment': environment,
               'environment_gate': 'PASS' if env_ok else 'UNVERIFIED_ENVIRONMENT',
               'surface_gate': 'PASS' if surface == '芝' else 'NOT_SUPPORTED_SURFACE',
               'collected_artifact': ref(out / f'{race_id}_collected.json'),
               'normalized_artifact': ref(out / f'{race_id}_normalized.json'),
               'source_issues': issues,
               'collected_audit': collected_audit,
               'structural_contract_errors': contract['errors'],
               'input_artifacts': {'feature': feature_ref},
               'source_audit_current_purpose': PURPOSE,
               'all_horses_retained':
                   len(entries) == race_row.get('starters'),
               'labels_separation': {
                   'outcomes_files_opened': False,
                   'forbidden_outcome_keys_in_inputs': forbidden},
               'source_bindings': bindings,
               'official_nonstarters': [
                   {'horse_number': str(x.get('horse_number', '')),
                    'horse_name': x.get('horse_name', '')}
                   for x in prep.get('official_nonstarters') or []],
               'career_source_checks': career_checks,
               'history_date_set_checks': date_checks,
               'horse_set_check': set_check}
    (out / f'{race_id}_audit.json').write_text(json.dumps(
        payload, ensure_ascii=False, indent=2))
    return payload


def load_dataset(dataset_dir: Path):
    preparations, races = {}, {}
    for month in MONTHS:
        for prep in load_jsonl_gz(
                dataset_dir / f'audit_preparation_2026-{month}.jsonl.gz'):
            preparations[prep['race_id']] = prep
    for race in load_jsonl_gz(dataset_dir / 'races.jsonl.gz'):
        races[race['race_id']] = race
    index = {(row['url'], row['sha256']) for row in
             load_jsonl_gz(dataset_dir / 'source_index.jsonl.gz')}
    return preparations, races, index


def run_audit(dataset_dir: Path, out: Path, race_ids: list | None = None) -> dict:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'Output directory already populated: {out}')
    manifest_check = verify_archive_manifest(dataset_dir)
    if not manifest_check['all_match']:
        raise SystemExit('Archive manifest verification failed; refusing to audit')
    verifier = run_dataset_verifier(dataset_dir)
    if verifier.get('status') != 'PASS':
        raise SystemExit('Dataset verifier did not PASS; refusing to audit')
    out.mkdir(parents=True, exist_ok=True)

    preparations, races, index_set = load_dataset(dataset_dir)
    candidates = select_candidates(preparations, races)
    if race_ids:
        wanted = set(race_ids)
        candidates = [c for c in candidates if c['race_id'] in wanted]
    candidate_ids = {c['race_id'] for c in candidates}
    features, feature_files = {}, {}
    for month in MONTHS:
        path = dataset_dir / f'features_2026-{month}.jsonl.gz'
        for record in load_jsonl_gz(path):
            if record['race_id'] in candidate_ids:
                features[record['race_id']] = record
                feature_files[record['race_id']] = path

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    payloads, execution_log = [], []
    for candidate in candidates:
        race_id = candidate['race_id']
        feature = features[race_id]
        base = {'race_id': race_id,
                'record_type': 'retrospective_reconstructed',
                'production_learning_eligible': False, 'model_ready': False,
                'model_calculations_executed': False}
        try:
            payload = audit_one_race(
                race_id, feature, preparations[race_id], races[race_id], out,
                index_set, base, ref(feature_files[race_id]))
        except Exception as exc:  # per-race containment; never fabricate
            payload = {**base, 'gate': 'AUDIT_ERROR', 'audit_executed': False,
                       'split': candidate['split'],
                       'segment': {'surface': candidate['surface'],
                                   'venue': candidate['track'],
                                   'distance': candidate['distance'],
                                   'band': distance_band(candidate['surface'],
                                                         candidate['distance'])},
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
                  ROOT / 'scripts/audit_20261024_strict_gate.py',
                  Path(__file__).resolve()]
    (out / 'audit_code_snapshot.json').write_text(json.dumps(
        {'files': [ref(p) for p in code_paths]}, ensure_ascii=False, indent=2))

    admitted = [p for p in payloads if p.get('model_ready')]
    def cells(items):
        counter = Counter(
            f"{p['segment']['surface']}|{p['segment']['venue']}|{p['segment']['distance']}"
            for p in items)
        return [{'segment': key, 'races': n}
                for key, n in counter.most_common()]

    def band_cells(items):
        counter = Counter(
            f"{p['segment']['surface']}|{p['segment']['band']}" for p in items)
        return [{'segment': key, 'races': n}
                for key, n in counter.most_common()]

    summary = {
        'role': 'data_integrity_auditor', 'purpose': PURPOSE,
        'dataset_source': {**DATASET_SOURCE,
                           'collection_sha256': manifest_check['collection_sha256']},
        'race_count': len(payloads),
        'batch_complete': all(p.get('audit_executed') for p in payloads),
        'audited_at': stamp,
        'preflight': {'archive_manifest_verification': manifest_check,
                      'dataset_verifier': verifier,
                      'source_index_records': len(index_set)},
        'candidate_selection': {
            'rule': "audit_preparation.state == 'PASS' (split, surface, venue "
                    "and distance joined from races.jsonl.gz)",
            'candidates': len(candidates),
            'by_split': dict(Counter(c['split'] for c in candidates)),
            'by_surface': dict(Counter(c['surface'] for c in candidates))},
        'gate_counts': dict(Counter(p['gate'] for p in payloads)),
        'core_gate_counts': dict(Counter(
            (p.get('collected_audit') or {}).get('gate', 'NOT_RUN')
            for p in payloads)),
        'gate_counts_by_surface': {
            surface: dict(Counter(p['gate'] for p in payloads
                                  if p['segment']['surface'] == surface))
            for surface in ('芝', 'ダート', '障害')},
        'gate_counts_by_split': {
            split: dict(Counter(p['gate'] for p in payloads
                                if p.get('split') == split))
            for split in ('train', 'validation', 'test')},
        'surface_gate_counts': dict(Counter(
            p.get('surface_gate', 'NOT_RUN') for p in payloads)),
        'pool': {
            'labels': {'usage': 'posthoc_exploratory_training_only',
                       'production_eligible': False},
            'previous': {'turf_races': 18, 'dirt_races': 0, 'jump_races': 0,
                         'composition': 'fixed evaluation 6 + 2026-10-25 PASS 10 '
                                        '+ 2026-10-24 PASS 2 (workstream C)',
                         'largest_cell': {'segment': '芝|新潟|2000', 'races': 3}},
            'admitted_race_ids': [p['race_id'] for p in admitted],
            'new_totals': {
                'turf_races': 18 + sum(1 for p in admitted
                                       if p['segment']['surface'] == '芝'),
                'dirt_races': sum(1 for p in admitted
                                  if p['segment']['surface'] == 'ダート'),
                'jump_races': sum(1 for p in admitted
                                  if p['segment']['surface'] == '障害')},
            'admitted_segment_cells': cells(admitted),
            'admitted_band_cells': band_cells(admitted),
            'candidate_segment_cells': cells(payloads),
            'candidate_band_cells': band_cells(payloads)},
        'races': [{'race_id': p['race_id'], 'gate': p['gate'],
                   'core_gate': (p.get('collected_audit') or {}).get(
                       'gate', 'NOT_RUN'),
                   'model_ready': p.get('model_ready', False),
                   'split': p.get('split'),
                   'segment': p.get('segment'),
                   'structural_contract_error_count':
                       len(p.get('structural_contract_errors') or []),
                   'source_bindings': p.get('source_bindings'),
                   'audit': ref(out / f"{p['race_id']}_audit.json")}
                  for p in payloads],
        'model_ready_race_ids': [p['race_id'] for p in admitted],
        'approvals_used': [], 'model_calculations_executed': False,
        'method_parity': {
            'reference': 'scripts/audit_20261024_strict_gate.py (workstream C); '
                         'report/research/20261025_training_20261028T040209Z/audit/',
            'adaptations': [
                'Archive verified against manifest.json and verify_dataset.py '
                'before auditing; abort on any mismatch.',
                'Candidate rule: audit_preparation.state == PASS (298 races).',
                'Entries projected with workstream C project_entry; archive '
                'integer horse_number/frame_number coerced to str.',
                'Horse-set check recomputed from cross-source agreement '
                '(feature start statuses vs preparation counts/official '
                'non-starters vs races.jsonl.gz counts); outcomes files are '
                'never opened.',
                'Structural contract copied with the batch-global TARGET '
                'replaced by each race\'s own target date.',
                'Environment selection mirrors workstream C semantics over '
                'the archive\'s per-venue-day record; post time constructed '
                'strictly from race_info.',
                'No source bodies exist in the archive, so the core reparse '
                'is UNVERIFIED for every race by construction; provenance is '
                'bound by (url, sha256) membership in the verified source '
                'index and recorded per race, without changing the gate.',
            ]},
    }
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({'output_dir': str(out),
                      'gate_counts': summary['gate_counts'],
                      'model_ready': summary['model_ready_race_ids']},
                     ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', type=Path, default=DATASET_DIR)
    parser.add_argument('--output-dir', type=Path, default=None,
                        help='New audit output directory (default: '
                             'report/research/dataset_20260502_20260927_'
                             'training_<UTC>Z/audit)')
    parser.add_argument('--race-id', action='append', default=None,
                        help='Restrict to this candidate race id (repeatable)')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = args.output_dir or (ROOT / 'report' / 'research'
                              / f'dataset_20260502_20260927_training_{stamp}'
                              / 'audit')
    run_audit(args.dataset_dir, out, args.race_id)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
