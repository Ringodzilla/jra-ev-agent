"""Honest acquisition records for the data_collector role.

The recorder only ever stores attempts the collector actually made, with the
outcome it actually observed. Nothing here fetches, infers or fills values:
an unresolved field stays unresolved, and the acquisition report says which
routes were tried for its exact scope and what they returned.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re

# Mirror of src.data_integrity._RAW_FIELDS: the fields the audit checks.
AUDITED_FIELDS = (
    'date', 'course', 'race_name', 'distance', 'history_surface', 'track_condition',
    'position', 'time', 'last_3f', 'passing_order', 'popularity', 'weight',
    'jockey', 'pace', 'weather', 'odds',
)

_EMPTY_VALUES = {'', 'None', 'null', '-', '―', '不明'}

# Jurisdiction -> acquisition routes this system can actually run in
# production. ``official_route_registered`` is False where the only route is
# the JRA official history page itself: no jurisdiction-specific official
# source is wired up, so an absence there must never be dressed up as a
# checked-and-empty official sectional source.
ROUTE_REGISTRY: dict[str, dict] = {
    'france': {
        'routes': ['jra_horse_history', 'france_galop_tracking'],
        'official_route_registered': True,
    },
    'hong_kong': {
        'routes': ['jra_horse_history', 'hkjc_sectionals'],
        'official_route_registered': True,
    },
    'uk_ireland': {
        'routes': ['jra_horse_history'],
        'official_route_registered': False,
        'reason': 'Imperial sectional proxy is research-only and is not a '
                  'production acquisition route in this system.',
    },
    'qatar': {
        'routes': ['jra_horse_history'],
        'official_route_registered': False,
    },
    'uae': {
        'routes': ['jra_horse_history'],
        'official_route_registered': False,
    },
    'saudi_arabia': {
        'routes': ['jra_horse_history'],
        'official_route_registered': False,
    },
    'usa': {
        'routes': ['jra_horse_history'],
        'official_route_registered': False,
    },
    'other_overseas': {
        'routes': ['jra_horse_history'],
        'official_route_registered': False,
    },
    'japan': {
        'routes': ['jra_race_detail', 'jra_horse_history'],
        'official_route_registered': True,
    },
}

# (jurisdiction, course/race_name markers), checked in this order.
_JURISDICTION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('qatar', ('カタール', 'ドーハ', 'アミール')),
    ('uae', ('ドバイ', 'メイダン')),
    ('hong_kong', ('香港', 'シャティン', 'ハッピーバレー')),
    ('france', ('仏', 'ロンシャン', 'シャンティイ', 'ドーヴィル')),
    ('uk_ireland', ('英', '愛', 'イギリス', 'アイルランド')),
    ('usa', ('米', 'アメリカ')),
)


def jurisdiction_for_row(row: dict) -> str:
    """Classify a history row's jurisdiction from its course/race_name text."""
    text = f"{row.get('course', '')} {row.get('race_name', '')}"
    for jurisdiction, markers in _JURISDICTION_MARKERS:
        if any(marker in text for marker in markers):
            return jurisdiction
    return 'japan'


def _is_empty(value: object) -> bool:
    return value is None or str(value).strip() in _EMPTY_VALUES


def iso_date(value: object) -> str:
    """Normalize a Japanese or ISO date string to ISO; pass through the rest."""
    text = str(value or '').strip()
    match = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if match:
        return date(*map(int, match.groups())).isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return text


class AcquisitionRecorder:
    """Append-only record of acquisition attempts actually performed."""

    def __init__(self) -> None:
        self._attempts: list[dict] = []
        self._registered_artifacts: list[dict] = []

    def record(self, route, *, url='', outcome, race_id='', horse_id='',
               horse_number='', run_index='', date='', fields=(),
               artifact_sha256='', artifact_path='', reason='', route_status=''):
        attempt = {
            'race_id': str(race_id),
            'horse_id': str(horse_id),
            'horse_number': str(horse_number),
            'run_index': str(run_index),
            'date': str(date),
            'fields': [str(field) for field in fields],
            'route': str(route),
            'url': str(url),
            'outcome': str(outcome),
            'artifact_sha256': str(artifact_sha256),
            'reason': str(reason),
        }
        if artifact_path:
            attempt['artifact_path'] = str(artifact_path)
        if route_status:
            attempt['route_status'] = str(route_status)
        self._attempts.append(attempt)
        return attempt

    def register_artifact(self, path, sha256, *, kind, url='', race_id=''):
        """Register a real stored file as run evidence (not an attempt)."""
        entry = {'path': str(path), 'sha256': str(sha256), 'kind': str(kind),
                 'url': str(url), 'race_id': str(race_id)}
        if entry not in self._registered_artifacts:
            self._registered_artifacts.append(entry)
        return entry

    def attempts(self) -> list[dict]:
        return [dict(attempt, fields=list(attempt['fields'])) for attempt in self._attempts]

    def routes(self) -> list[dict]:
        grouped: dict[str, dict] = {}
        for attempt in self._attempts:
            entry = grouped.setdefault(attempt['route'], {'outcomes': set(), 'attempts': 0})
            entry['outcomes'].add(attempt['outcome'])
            entry['attempts'] += 1
        return [{'route': route, 'outcomes': sorted(entry['outcomes']),
                 'attempts': entry['attempts']}
                for route, entry in sorted(grouped.items())]

    def artifacts(self) -> list[dict]:
        """Unique real files bound to recorded attempts or registered directly."""
        seen: dict[tuple[str, str], dict] = {}
        for attempt in self._attempts:
            path = attempt.get('artifact_path', '')
            sha = attempt.get('artifact_sha256', '')
            if path and sha:
                seen.setdefault((path, sha), {
                    'path': path, 'sha256': sha, 'kind': attempt['route'],
                    'url': attempt.get('url', ''), 'race_id': attempt.get('race_id', ''),
                })
        for entry in self._registered_artifacts:
            seen.setdefault((entry['path'], entry['sha256']), dict(entry))
        return [seen[key] for key in sorted(seen)]

    def artifacts_for_race(self, race_id: str) -> list[dict]:
        return [{'path': entry['path'], 'sha256': entry['sha256'],
                 'kind': entry['kind'], 'url': entry['url']}
                for entry in self.artifacts() if entry['race_id'] == str(race_id)]


def _attempt_matches(attempt: dict, row: dict, field: str, row_iso_date: str) -> bool:
    if str(attempt.get('race_id', '')) != str(row.get('race_id', '')):
        return False
    attempt_horse = str(attempt.get('horse_id', ''))
    if attempt_horse and attempt_horse != str(row.get('horse_id', '')):
        return False
    attempt_date = str(attempt.get('date', ''))
    if attempt_date and attempt_date not in {str(row.get('date', '')), row_iso_date}:
        return False
    return field in (attempt.get('fields') or [])


def _unresolved_fields_for_row(row: dict) -> list[str]:
    jump = (str(row.get('history_surface', '')) == '障害'
            or '障害' in str(row.get('race_name', '')))
    unresolved = []
    for field in AUDITED_FIELDS:
        if str(row.get(field + '_status', '')) == 'NOT_APPLICABLE':
            continue
        if field == 'last_3f' and jump:
            # A jump race's official record carries average-1F, never a
            # final-3F sectional; its absence is non-applicability.
            continue
        if _is_empty(row.get(field)) or (
                field == 'last_3f' and str(row.get('last_3f_source', '')) == 'fallback'):
            unresolved.append(field)
    return unresolved


def compute_unresolved_observations(rows, recorder) -> list[dict]:
    """Exactly one honest observation per unresolved (scope, field).

    The reason is composed only from recorded facts: the row's own missing
    reason, the outcomes of routes actually attempted for that scope, and the
    route-registry status of the row's jurisdiction.
    """
    attempts = recorder.attempts() if recorder is not None else []
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for row in rows:
        row_iso = iso_date(row.get('date', ''))
        jurisdiction = jurisdiction_for_row(row)
        registry = ROUTE_REGISTRY.get(jurisdiction, {})
        for field in _unresolved_fields_for_row(row):
            parts: list[str] = []
            missing_reason = str(row.get(field + '_missing_reason', '') or '').strip()
            if missing_reason:
                parts.append(missing_reason)
            for attempt in attempts:
                if not _attempt_matches(attempt, row, field, row_iso):
                    continue
                part = f"{attempt['route']}: {attempt['outcome']}"
                if attempt.get('reason'):
                    part += f" ({attempt['reason']})"
                parts.append(part)
            if jurisdiction != 'japan' and not registry.get('official_route_registered', False):
                parts.append(
                    f"no official source route registered in this system for {jurisdiction}; "
                    "JRA official history is the only route and lists no value")
                if registry.get('reason'):
                    parts.append(str(registry['reason']))
            if not parts:
                parts.append('No observed value was collected for this field and no '
                             'missing reason or route attempt was recorded for this scope')
            key = (str(row.get('race_id', '')), str(row.get('horse_id', '')),
                   str(row.get('horse_number', '')), str(row.get('run_index', '')),
                   row_iso, field)
            if key not in merged:
                merged[key] = {'race_id': key[0], 'horse_id': key[1], 'horse_number': key[2],
                               'run_index': key[3], 'date': row_iso, 'field': field,
                               '_parts': set()}
                order.append(key)
            merged[key]['_parts'].update(parts)
    observations = []
    for key in order:
        entry = merged[key]
        observations.append({
            'race_id': entry['race_id'], 'horse_id': entry['horse_id'],
            'horse_number': entry['horse_number'], 'run_index': entry['run_index'],
            'date': entry['date'], 'field': entry['field'],
            'reason': '; '.join(sorted(entry['_parts'])),
        })
    return observations


def build_acquisition_report(*, race_id, rows, recorder, generated_at) -> dict:
    """Build the data_collector acquisition report for one race's final rows."""
    routes = recorder.routes()
    if not routes:
        raise ValueError('Acquisition report requires at least one recorded route attempt; '
                         'refusing to fabricate routes that were not attempted')
    observations = compute_unresolved_observations(rows, recorder)
    horse_names = {}
    for row in rows:
        key = (str(row.get('race_id', '')), str(row.get('horse_id', '')),
               str(row.get('horse_number', '')), str(row.get('run_index', '')),
               iso_date(row.get('date', '')))
        horse_names.setdefault(key, str(row.get('horse_name', '')))
    unresolved_fields: dict[str, list[str]] = {}
    for observation in observations:
        key = (observation['race_id'], observation['horse_id'], observation['horse_number'],
               observation['run_index'], observation['date'])
        scope = (f"{horse_names.get(key, '')} {observation['date']} "
                 f"run{observation['run_index']}")
        unresolved_fields.setdefault(observation['field'], []).append(scope)
    return {
        'role': 'data_collector',
        'race_id': str(race_id),
        'generated_at': str(generated_at),
        'routes': routes,
        'unresolved_fields': {field: sorted(scopes)
                              for field, scopes in sorted(unresolved_fields.items())},
        'unresolved_observations': observations,
        'evidence_files': [],
    }


def write_acquisition_bundle(out_dir, report, evidence: dict[str, dict]) -> dict:
    """Write evidence JSONs beside the report and return its SHA-bound ref.

    Evidence payloads may only reference real files: downstream validation
    (src.history_exceptions._attempts) recursively checks every {path, sha256}
    reference inside them against the files on disk.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for name, payload in sorted((evidence or {}).items()):
        filename = str(name)
        if filename != Path(filename).name or not filename:
            raise ValueError(f'Evidence filename must be a plain file name: {name!r}')
        (out / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8')
        names.append(filename)
    final_report = dict(report)
    final_report['evidence_files'] = sorted(names)
    data = json.dumps(final_report, ensure_ascii=False, indent=2,
                      sort_keys=True).encode('utf-8')
    report_path = out / 'acquisition_report.json'
    report_path.write_bytes(data)
    return {'path': str(report_path.resolve()),
            'sha256': hashlib.sha256(data).hexdigest()}
