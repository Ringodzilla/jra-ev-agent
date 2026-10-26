"""Pure, source-bound field acquisition evidence; declarations never prove absence."""
from __future__ import annotations

from collections import Counter
import re


def _empty(value: object) -> bool:
    return value is None or str(value).strip() in {'', 'None', 'null', '-', '―', '不明'}


def _identity(row: dict, field: str) -> dict:
    return {**{key: row.get(key, '') for key in
               ('race_id', 'horse_id', 'horse_number', 'run_index', 'date')}, 'field': field}


def missing_source_coverage(collected: dict, fields: tuple[str, ...]) -> dict:
    records = [dict(_identity(row, field), state='SOURCE_UNVERIFIED',
                    input_value=row.get(field), observed_values=[], source_artifacts=[],
                    reason='No matched hash-verified, pre-target source row')
               for row in collected.get('rows', []) for field in fields]
    return {'status': 'BLOCKED', 'records': records,
            'counts': dict(Counter(r['state'] for r in records))}


def field_coverage(row: dict, normalized: dict, field: str, originals: list[dict],
                   collected: dict) -> dict:
    """Called only after source SHA validation and horse/date/cutoff matching."""
    value = normalized.get(field)
    observed = [o for o in originals if not _empty(o.get(field))
                and not (field == 'last_3f' and o.get('_raw_last3f_source') == 'fallback')]
    sources = [o['_source_artifact'] for o in originals]
    record = dict(_identity(row, field), input_value=value,
                  observed_values=sorted({str(o[field]) for o in observed}),
                  source_artifacts=sources)
    if observed:
        return dict(record, state='AVAILABLE_BUT_NOT_COLLECTED' if _empty(value) else 'OBSERVED',
                    reason='Nonempty value reparsed from matched raw source')
    # A nonempty fallback remains subject to the separate explicit-consent gate.
    if not _empty(value):
        return dict(record, state='SUBSTITUTED',
                    reason='No observed source value; explicit substitution consent and source matching audited separately')
    if field == 'pace' and any(o.get('_pace_missing_reason') ==
                              'NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED' for o in originals):
        return dict(record, state='NOT_MEASURED',
                    reason='Validated official intervals have no exact 600m boundary; no interpolation')
    attempts = (collected.get('source_audit') or {}).get('attempts', [])
    matching = [a for a in attempts if all(str(a.get(k, '')) == str(row.get(k, ''))
                for k in ('race_id', 'horse_id', 'date')) and field in a.get('fields', [])]
    artifacts = (collected.get('source_audit') or {}).get('artifacts', [])
    bound = [artifact for artifact in artifacts if any(a.get('artifact_sha256') == artifact.get('sha256')
             for a in matching)]
    failed = any(re.search(r'パラメータ.*エラー|エラー.*013|403 Forbidden|404 Not Found',
                          artifact['html'], re.DOTALL) for artifact in bound)
    if failed:
        return dict(record, state='FETCH_FAILED', reason='Attempt bound to hash-verified raw error response')
    relevant_kinds = ({'history_result'} if field in {'pace', 'weather'} else
                      {'history_odds'} if field == 'odds' else
                      {'horse_history', 'race_detail', 'history_result'})
    if matching or any(s['kind'] in relevant_kinds for s in sources):
        return dict(record, state='SOURCE_FIELD_MISSING',
                    reason='Attempt recorded but no observed field; absence is not non-applicability')
    return dict(record, state='NOT_ATTEMPTED',
                reason='Matched source contains no observation and no field-specific recovery attempt')
