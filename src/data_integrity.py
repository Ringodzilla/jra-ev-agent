"""Read-only, fail-closed audit of collected model inputs and substitutions."""
from __future__ import annotations

import hashlib
import datetime as dt
import json
import math
import re
from collections import Counter

_RAW_FIELDS = ('date', 'course', 'race_name', 'distance', 'history_surface', 'track_condition',
               'position', 'time', 'last_3f', 'passing_order', 'popularity', 'weight',
               'jockey', 'pace', 'weather', 'odds')
_REQUIRED = {'date', 'distance', 'position', 'time'}
_EFFECTS = {
    'date': ('default_35_days', 'days_since_last_run'),
    'distance': ('default_0', 'avg_distance,speed_score,distance_fit'),
    'position': ('default_10', 'ability_score,win_rate,top3_rate,consistency'),
    'time': ('default_0', 'speed_score,ability_score'),
    'last_3f': ('default_36', 'closing_strength,pace_score'),
    'passing_order': ('neutral_0.5', 'front_rate,pace_score,track_bias_score'),
    'popularity': ('default_10', 'avg_popularity,popularity_gap'),
    'weight': ('default_55', 'avg_weight,weight_score'),
    'odds': ('exclude_nonpositive_then_default_0', 'avg_odds,odds_trend,odds_volatility'),
    'course': ('exclude_empty_from_map', 'venue_match,course_score'),
    'history_surface': ('exclude_empty_from_map', 'surface_match,course_score'),
    'track_condition': ('exclude_empty_from_map_or_neutral_0.5', 'track_condition_score,course_score'),
    'jockey': ('exclude_empty_from_map', 'jockey_match,jockey_score'),
}


def data_input_sha256(collected: dict, race_configs: object) -> str:
    """Bind approvals to model rows/entries/config, excluding combo-odds blobs."""
    payload = {'rows': collected.get('rows', []), 'entries': collected.get('entries', []),
               'race_configs': race_configs}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _missing(value: object) -> bool:
    return value is None or str(value).strip() in {'', 'None', 'null', '-', '―', '不明'}


def _number(value: object) -> bool:
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _date_key(value: object) -> str:
    text = str(value or '')
    match = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if match:
        year, month, day = match.groups()
        return f'{year}-{int(month):02d}-{int(day):02d}'
    return text


def _config(configs: object, race: str) -> dict:
    if isinstance(configs, list):
        return next((c for c in configs if str(c.get('race_id', '')) == race), {})
    if isinstance(configs, dict):
        if race in configs and isinstance(configs[race], dict):
            return configs[race]
        return configs if 'race_id' in configs or 'career_starts_by_horse_number' in configs else {}
    return {}


def verify_source_artifacts(collected: dict) -> dict:
    """Reparse hash-bound raw HTML, never trust a caller's verified flag."""
    from jra_scraper.parser import JRAParser
    from jra_scraper.validation import _normalize_row

    artifacts = (collected.get('source_audit') or {}).get('artifacts', [])
    reparsed: dict[tuple[str, str, str], list[dict]] = {}
    for artifact in artifacts:
        html = artifact.get('html')
        if not isinstance(html, str) or hashlib.sha256(html.encode()).hexdigest() != artifact.get('sha256'):
            return {'status': 'UNVERIFIED', 'mismatches': []}
        try:
            parser = JRAParser(base_url='https://jra.jp')
            race_id = str(artifact.get('race_id', ''))
            if artifact.get('kind') == 'horse_history':
                parsed = parser.parse_horse_last5(
                    html, race_id, str(artifact.get('horse_id', '')),
                    str(artifact.get('horse_name', '')), str(artifact.get('horse_url', '')))
            elif artifact.get('kind') == 'race_detail':
                horses = parser.parse_race_detail(html, race_id, str(artifact.get('race_name', '')))
                parsed = []
                for horse in horses:
                    for history in horse.embedded_history:
                        parsed.append(dict(history, race_id=race_id, horse_id=horse.horse_id,
                                           horse_number=horse.horse_number, horse_name=horse.horse_name))
            elif artifact.get('kind') == 'history_result':
                parsed = _history_result_observations(html, artifact)
            elif artifact.get('kind') == 'history_odds':
                parsed = _history_odds_observations(html, artifact)
            else:
                continue
        except (ValueError, TypeError, KeyError):
            return {'status': 'UNVERIFIED', 'mismatches': []}
        for raw_row in parsed:
            row = _normalize_row(raw_row)
            row['date'] = _date_key(row.get('date'))
            row['_raw_last3f_source'] = raw_row.get('last_3f_source', '')
            key = tuple(str(row.get(k, '')) for k in ('race_id', 'horse_id', 'date'))
            reparsed.setdefault(key, []).append(row)
    rows = collected.get('rows', [])
    if not rows:
        return {'status': 'UNVERIFIED', 'mismatches': []}
    mismatches = []
    detected_imputations = []
    for row in rows:
        normalized_input = _normalize_row(row)
        normalized_input['date'] = _date_key(normalized_input.get('date'))
        key = tuple(str(normalized_input.get(k, '')) for k in ('race_id', 'horse_id', 'date'))
        originals = reparsed.get(key, [])
        target_date = normalized_input.get('target_race_date', '')
        if not target_date:
            mismatches.append({'row': row, 'field': 'target_race_date', 'source_value': 'missing_cutoff'})
            continue
        if target_date:
            originals = [r for r in originals if r.get('date', '') < target_date]
        if not originals:
            mismatches.append({'row': row, 'field': 'source_row', 'source_value': 'unmatched_horse_date_or_after_target'})
            continue
        for field in _RAW_FIELDS:
            value = str(normalized_input.get(field) or '').strip()
            values = [str(original.get(field) or '').strip() for original in originals]
            if value not in values:
                mismatches.append({'row': row, 'field': field, 'source_value': values})
        if (all(r.get('_raw_last3f_source') == 'fallback' for r in originals)
                and row.get('last_3f_source') != 'fallback'):
            detected_imputations.append({'row': row, 'field': 'last_3f', 'method': 'parser_last3f_fallback'})
    return {'status': 'MISMATCH' if mismatches else 'PASS', 'mismatches': mismatches,
            'detected_imputations': detected_imputations}


def _history_result_observations(html: str, artifact: dict) -> list[dict]:
    """Recover observed sectionals from an official historical result, not a default."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    dates = soup.select('.cell.date')
    matches = [tr for tr in soup.select('tr') if tr.select_one('td.horse a')
               and tr.select_one('td.horse a').get_text(strip=True) == artifact.get('horse_name')]
    if len(dates) != 1 or len(matches) != 1:
        raise ValueError('Historical result horse/date is not unique')
    match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', dates[0].get_text(' ', strip=True))
    if not match:
        raise ValueError('Historical result date is unverified')
    day = dt.date(*(int(value) for value in match.groups())).isoformat()
    row = matches[0]
    def cell(selector):
        node = row.select_one(selector)
        return node.get_text(' ', strip=True) if node else ''
    weather = soup.select_one('li.weather .txt')
    return [dict(race_id=str(artifact.get('race_id', '')), horse_id=str(artifact.get('horse_id', '')),
                 horse_name=artifact.get('horse_name', ''), date=day,
                 last_3f=cell('td.f_time'), last_3f_source='official_historical_result',
                 passing_order='-'.join(node.get_text(strip=True) for node in row.select('td.corner li')),
                 position=cell('td.place'), time=cell('td.time'), jockey=cell('td.jockey'),
                 weight=cell('td.weight'), popularity=cell('td.pop'),
                 weather=weather.get_text(strip=True) if weather else '')]


def _history_odds_observations(html: str, artifact: dict) -> list[dict]:
    """Read a unique horse's observed historical win odds and independent page date."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    dates = soup.select('.cell.date')
    matches = [tr for tr in soup.select('tr') if tr.select_one('td.horse')
               and tr.select_one('td.horse').get_text(strip=True) == artifact.get('horse_name')]
    if len(dates) != 1 or len(matches) != 1:
        raise ValueError('Historical odds horse/date is not unique')
    match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', dates[0].get_text(' ', strip=True))
    if not match:
        raise ValueError('Historical odds date is unverified')
    day = dt.date(*(int(value) for value in match.groups())).isoformat()
    odds = matches[0].select_one('td.odds_tan')
    value = odds.get_text(strip=True).replace(',', '') if odds else ''
    if not _number(value):
        raise ValueError('Historical win odds are not observed positive numbers')
    return [dict(race_id=str(artifact.get('race_id', '')), horse_id=str(artifact.get('horse_id', '')),
                 horse_name=artifact.get('horse_name', ''), date=day, odds=value)]


def audit_collected_data(collected: dict, race_configs: object, approvals: list | None = None,
                         purpose: str = 'prediction') -> dict:
    """Audit missing raw fields separately from feature fallback authorization.

    Does not fetch, mutate, or infer source facts. Original-source verification
    requires hash-bound raw HTML reparse; quality flags are not source comparison.
    """
    rows = collected.get('rows', [])
    entries = collected.get('entries', [])
    quality = collected.get('quality_report') or {}
    digest = data_input_sha256(collected, race_configs)
    findings: list[dict] = []
    missing: list[dict] = []
    counts: list[dict] = []
    required_missing = not entries
    unknown_counts = False

    def finding(row: dict, field: str, method: str, impact: str, required: bool = False) -> None:
        identity = {k: str(row.get(k, '')) for k in ('race_id', 'horse_id', 'horse_number', 'run_index')}
        identity.update(field=field, method=method)
        stable = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        record = dict(identity, finding_id=stable, feature_impact=impact,
                      required_missing=required, approved=False)
        if not any(f['finding_id'] == stable for f in findings):
            findings.append(record)

    for row in rows:
        neutral = str(row.get('neutral_history_fallback', '')).lower() in {'true', '1'}
        if neutral:
            finding(row, 'history', 'neutral_history_fallback', 'all_history_features')
            continue
        if str(row.get('last_3f_source', '')).strip() == 'fallback':
            finding(row, 'last_3f', 'parser_last3f_fallback', 'closing_strength,pace_score')
        for field in _RAW_FIELDS:
            absent = _missing(row.get(field))
            if field == 'history_surface' and any(x in str(row.get('distance', '')) for x in ('芝', 'ダ', '障')):
                absent = False
            invalid = field in {'distance', 'position', 'time', 'last_3f', 'popularity', 'weight', 'odds'} and not _number(row.get(field))
            if field == 'date' and not absent:
                try:
                    date_text = str(row[field])
                    match = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_text)
                    if match:
                        year, month, day = match.groups()
                        date_text = f'{year}-{int(month):02d}-{int(day):02d}'
                    dt.date.fromisoformat(date_text)
                except ValueError:
                    invalid = True
            if field == 'passing_order' and not absent:
                invalid = not any(p.strip().isdigit() for p in str(row[field]).replace('→', '-').split('-'))
            if absent or invalid:
                missing.append({**{k: str(row.get(k, '')) for k in ('race_id', 'horse_number', 'run_index')},
                                'field': field, 'state': 'missing' if absent else 'invalid',
                                'model_used': field in _EFFECTS})
                if field in _EFFECTS:
                    method, impact = _EFFECTS[field]
                    finding(row, field, method, impact, field in _REQUIRED)
                required_missing |= field in _REQUIRED
        if '障害' in str(row.get('race_name', '')) or str(row.get('history_surface')) == '障害' or str(row.get('target_surface')) == '障害':
            finding(row, 'last_3f', 'neutralize_jump_sectional_36', 'closing_strength,pace_score')

    issues = quality.get('issues', [])
    fallback_count = quality.get('last3f_fallback_rows', 0)
    fallback_issues = [i for i in issues if isinstance(i, dict) and i.get('code') == 'last3f_fallback']
    localized = 0
    for issue in fallback_issues:
        context = issue.get('context') or {}
        matched = [r for r in rows if any(context.get(k) for k in ('horse_id', 'horse_number', 'horse_name'))
                   and all(not context.get(k) or str(context[k]) == str(r.get(k, ''))
                           for k in ('race_id', 'horse_id', 'horse_number', 'horse_name', 'run_index'))]
        for row in matched:
            finding(row, 'last_3f', 'parser_last3f_fallback', 'closing_strength,pace_score')
        localized += bool(matched)
    if int(fallback_count or 0) > localized or len(fallback_issues) > localized:
        # Unknown horse is explicitly scoped per race; no cross-race approval reuse.
        for race in sorted({str(e.get('race_id', '')) for e in entries}):
            finding({'race_id': race}, 'last_3f', 'parser_last3f_fallback_unlocalized',
                    'closing_strength,pace_score')
    for entry in entries:
        race = str(entry.get('race_id', ''))
        horse = str(entry.get('horse_number', ''))
        history = [r for r in rows if str(r.get('race_id', '')) == race and str(r.get('horse_number', '')) == horse
                   and str(r.get('neutral_history_fallback', '')).lower() not in {'true', '1'}]
        config = _config(race_configs, race)
        career = config.get('career_starts_by_horse_number', {}).get(horse)
        expected = min(5, int(career)) if career is not None else None
        if expected is None:
            for action in quality.get('missing_data_repair_actions', []):
                same_horse = (str(action.get('horse_number', '')) == horse
                              or bool(entry.get('horse_id')) and str(action.get('horse_id', '')) == str(entry.get('horse_id')))
                if same_horse and str(action.get('race_id', race)) == race:
                    if action.get('required_history_count') is not None:
                        expected = int(action['required_history_count'])
        state = 'UNVERIFIED' if expected is None else ('COMPLETE' if len(history) >= expected else 'MISSING')
        counts.append({'race_id': race, 'horse_number': horse, 'collected': len(history),
                       'required': expected, 'status': state})
        unknown_counts |= expected is None
        if expected is not None and len(history) < expected:
            required_missing = True
        if not history:
            finding(entry, 'history', 'neutral_history_fallback', 'all_history_features')
        for field, method, impact in (
            ('assigned_weight', 'default_55', 'weight_score'),
            ('current_popularity', 'history_average_or_10', 'popularity_gap'),
            ('current_odds', 'default_0', 'market_support,EV'),
        ):
            value = entry.get(field)
            if field == 'current_odds':
                value = value or entry.get('win_odds')
            if not _number(value):
                finding(entry, field, method, impact)

    source_check = verify_source_artifacts(collected)
    for recovered in source_check.get('detected_imputations', []):
        finding(recovered['row'], recovered['field'], recovered['method'], 'closing_strength,pace_score')
    race_ids = sorted({str(e.get('race_id', '')) for e in entries})
    for race in race_ids:
        race_findings = [f for f in findings if f['race_id'] == race]
        ids = sorted(f['finding_id'] for f in race_findings)
        approved = any(isinstance(a, dict) and a.get('approved_by') == 'user'
                       and a.get('status') == 'APPROVED' and a.get('race_id') == race
                       and a.get('purpose') == purpose and a.get('input_sha256') == digest
                       and isinstance(a.get('evidence'), str) and a['evidence'].strip()
                       and isinstance(a.get('finding_ids'), list) and sorted(a['finding_ids']) == ids
                       for a in approvals or [])
        if approved:
            for f in race_findings:
                f['approved'] = True
    unapproved = [f for f in findings if not f['approved']]
    source_verified = source_check['status'] == 'PASS'
    gate = 'BLOCKED_MISSING_DATA' if required_missing else (
        'BLOCKED_UNAPPROVED_IMPUTATION' if unapproved else (
            'PASS' if source_verified and not unknown_counts else 'UNVERIFIED'))
    return {'schema_version': 1, 'status': gate, 'gate': gate, 'purpose': purpose,
            'verification_issues': source_check['mismatches'],
            'recovered_imputation_provenance': source_check.get('detected_imputations', []),
            'input_sha256': digest, 'race_ids': race_ids, 'findings': findings,
            'missing_fields': missing, 'missing_field_counts': dict(Counter(m['field'] for m in missing)),
            'history_counts': counts, 'history_counts_verified': not unknown_counts,
            'unapproved_finding_count': len(unapproved), 'source_audit_status': 'VERIFIED_REPARSE' if source_verified else 'UNVERIFIED',
            'raw_provenance_status': 'VERIFIED_REPARSE' if source_verified else 'UNVERIFIED',
            'limitations': ['Normalized inputs only; original-source parser omissions are not verified.',
                            'Collector quality flags and nonempty numeric values do not prove observed origin.',
                            'Raw history reparse verifies reproducibility, not independent parser completeness or current entry provenance.']}
