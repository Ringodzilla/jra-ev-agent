"""Pure, source-bound field acquisition evidence; declarations never prove absence.

SOURCE_ROUTE_UNAVAILABLE is a diagnostic classification, never a pass: the
field exists in the world (a timed race in Qatar has a finishing time), but
this system has no registered official source route for that jurisdiction,
and the one route that ran (the JRA official history) does not observe the
field. It is deliberately distinct from NOT_APPLICABLE, which is reserved
for fields that cannot exist for the observation itself (a jump race's
final-3F sectional, a straight course's corner positions). Reclassifying a
missing observation as NOT_APPLICABLE merely because no route reaches it
would redefine absence as inapplicability and let the gate pass on a
declaration — exactly what this module forbids. The gate's pass states in
src.data_integrity therefore exclude SOURCE_ROUTE_UNAVAILABLE.
"""
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
    # Only reparsed, hash-verified originals can prove non-applicability.
    # A caller's marker or a blank cell is never enough.
    semantic_na = [o for o in originals if o.get(field + '_status') == 'NOT_APPLICABLE'
                   and _empty(o.get(field))]
    if semantic_na and observed:
        return dict(record, state='SOURCE_SEMANTIC_CONFLICT',
                    reason='Matched originals disagree on observation and non-applicability')
    if semantic_na:
        if not _empty(value):
            return dict(record, state='SOURCE_SEMANTIC_CONFLICT',
                        reason='Input contains a number for a source-proven nonapplicable field')
        return dict(record, state='NOT_APPLICABLE',
                    reason=semantic_na[0].get(field + '_missing_reason', ''),
                    semantic_status='NOT_APPLICABLE')
    if (field == 'last_3f' and _empty(value) and not observed
            and not any(o.get('last_3f_missing_reason') == 'CONFLICTING_SECTIONAL_UNITS'
                        for o in originals)
            and any(str(o.get('history_surface', '')) == '障害'
                    or '障害' in str(o.get('race_name', '')) for o in originals)):
        # A jump race's official record carries an average-1F column, never a
        # final-3F sectional; jra_scraper.history_observations encodes the
        # same unit rule. Once the hash-verified originals establish the race
        # as a jump race, the absence of a 3F value is non-applicability,
        # not an unobserved field.
        return dict(record, state='NOT_APPLICABLE',
                    reason='Jump race records carry average-1F, not a final-3F sectional',
                    semantic_status='NOT_APPLICABLE')
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
    route_blocked = [a for a in matching if a.get('route_status') == 'no_official_source_route']
    unavailable = [o for o in originals if o.get(field + '_status') in
                   {'NOT_OBSERVED', 'UNVERIFIED'} and _empty(o.get(field))]
    if unavailable:
        if route_blocked:
            # The only route's original does not observe the field, and the
            # collector recorded that no other official route is registered
            # for this jurisdiction. Still blocked; classified for diagnosis.
            return dict(record, state='SOURCE_ROUTE_UNAVAILABLE',
                        semantic_status=unavailable[0][field + '_status'],
                        reason=str(route_blocked[0].get('reason', '')).strip()
                               or 'No official source route is registered for this jurisdiction')
        return dict(record, state='SOURCE_FIELD_MISSING',
                    semantic_status=unavailable[0][field + '_status'],
                    reason=unavailable[0].get(field + '_missing_reason',
                                              'Source observation is not verified'))
    artifacts = (collected.get('source_audit') or {}).get('artifacts', [])
    bound = [artifact for artifact in artifacts if any(a.get('artifact_sha256') == artifact.get('sha256')
             for a in matching)]
    failed = any(re.search(r'パラメータ.*エラー|エラー.*013|403 Forbidden|404 Not Found',
                          artifact['html'], re.DOTALL) for artifact in bound)
    if failed:
        return dict(record, state='FETCH_FAILED', reason='Attempt bound to hash-verified raw error response')
    if route_blocked:
        return dict(record, state='SOURCE_ROUTE_UNAVAILABLE',
                    reason=str(route_blocked[0].get('reason', '')).strip()
                           or 'No official source route is registered for this jurisdiction')
    relevant_kinds = ({'history_result'} if field in {'pace', 'weather'} else
                      {'history_odds'} if field == 'odds' else
                      {'horse_history', 'race_detail', 'history_result'})
    if matching or any(s['kind'] in relevant_kinds for s in sources):
        return dict(record, state='SOURCE_FIELD_MISSING',
                    reason='Attempt recorded but no observed field; absence is not non-applicability')
    return dict(record, state='NOT_ATTEMPTED',
                reason='Matched source contains no observation and no field-specific recovery attempt')
