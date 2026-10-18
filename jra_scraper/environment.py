"""Capture observed JRA environment inputs with dates and immutable provenance."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .scraper import safe_filename
from .parser import JRAParser

JST = timezone(timedelta(hours=9))
SOURCES = {"cushion": "https://www.jra.go.jp/keiba/baba/_data_cushion.html",
           "moisture": "https://www.jra.go.jp/keiba/baba/_data_moist.html"}


def _measurement_time(text: str, year: int) -> str:
    match = re.fullmatch(r"(\d+)月(\d+)日（[^）]+）(\d+)時(\d+)分", text.strip())
    if not match:
        raise ValueError("Unknown official measurement time format")
    month, day, hour, minute = map(int, match.groups())
    return datetime(year, month, day, hour, minute, tzinfo=JST).isoformat()


def capture_environment(scraper, race_config: dict, rows: list[dict], directory: Path,
                        *, cache_only: bool = False, now: datetime | None = None) -> dict:
    race_id = str(race_config.get('race_id') or
                  f"{str(race_config['race_date']).replace('-', '')}_{race_config['track']}_{int(race_config['race_number']):02d}")
    root = directory / safe_filename(race_id)
    if cache_only:
        saved = sorted(root.glob('*.json'))
        return json.loads(saved[-1].read_text()) if saved else {'race_id': race_id, 'status': 'MISSING_CACHED_OBSERVATION'}
    if urlparse(str(race_config.get('source_url', ''))).hostname not in {'www.jra.go.jp', 'jra.go.jp', 'jra.jp', 'www.jra.jp'}:
        return {'race_id': race_id, 'status': 'UNSUPPORTED_SOURCE'}
    captured = now or datetime.now(timezone.utc)
    captured_jst = captured.astimezone(JST)
    day = str(race_config['race_date'])
    record = {'race_id': race_id, 'track': race_config['track'], 'date': day,
              'captured_at': captured.isoformat(), 'value_type': 'observed',
              'source_url': race_config['source_url'], 'sources': {}, 'errors': []}
    race_html = scraper.fetch(race_config['source_url'],
                              raw_name=f"environment_race_{captured.strftime('%Y%m%dT%H%M%S%f')}.html",
                              use_cache=False)
    weather, condition = JRAParser._extract_current_race_conditions(
        BeautifulSoup(race_html or '', 'html.parser'),
        target_surface=str(race_config.get('surface', '芝')),
    )
    record.update(weather=weather, track_condition=condition,
                  weather_captured_at=captured.isoformat() if weather and condition else '')
    if race_html:
        record['sources']['weather'] = {'url': race_config['source_url'],
                                       'raw_name': f"environment_race_{captured.strftime('%Y%m%dT%H%M%S%f')}.html",
                                       'sha256': hashlib.sha256(race_html.encode()).hexdigest()}
    for kind, url in SOURCES.items():
        raw_name = f"environment_{kind}_{captured.strftime('%Y%m%dT%H%M%S%f')}.html"
        html = scraper.fetch(url, raw_name=raw_name, use_cache=False)
        if not html:
            record['errors'].append(f'{kind}_fetch_failed')
            continue
        record['sources'][kind] = {'url': url, 'raw_name': raw_name,
                                   'sha256': hashlib.sha256(html.encode()).hexdigest()}
        soup = BeautifulSoup(html, 'html.parser')
        course = next((node for node in soup.select('div[title]') if node.get('title') == race_config['track']), None)
        unit = course.select_one('.unit') if course else None
        try:
            if unit is None:
                raise ValueError('Course measurement not found')
            stamp = _measurement_time(unit.select_one('.time').get_text(strip=True), int(day[:4]))
            if datetime.fromisoformat(stamp) > captured_jst:
                raise ValueError('Future measurement')
            record[f'{kind}_measured_at'] = stamp
            if kind == 'cushion':
                value = float(unit.select_one('.cushion').get_text(strip=True))
                if not 0 < value <= 20:
                    raise ValueError('Invalid cushion')
                record['cushion'] = value
            else:
                for surface in ('turf', 'dirt'):
                    for selector, location in (('mg', 'goal'), ('m4c', 'corner4')):
                        value = float(unit.select_one(f'.{surface} .{selector}').get_text(strip=True))
                        if not 0 <= value <= 100:
                            raise ValueError('Invalid moisture')
                        record[f'{surface}_moisture_{location}'] = value
        except (ValueError, AttributeError) as exc:
            record['errors'].append(f'{kind}_invalid: {exc}')
    required = ['weather', 'track_condition', 'weather_captured_at', 'moisture_measured_at',
                'cushion_measured_at', 'cushion', 'turf_moisture_goal', 'turf_moisture_corner4']
    record['missing_fields'] = [key for key in required if record.get(key) in (None, '')]
    record['same_day_measurements'] = all(str(record.get(f'{kind}_measured_at', '')).startswith(day)
                                           for kind in SOURCES)
    post_time = race_config.get('post_time')
    cutoff = datetime.fromisoformat(f'{day}T{post_time}:00+09:00') if post_time else None
    weather_at = record['weather_captured_at']
    weather_valid = bool(weather_at and captured_jst.date().isoformat() == day)
    record['cutoff_at'] = cutoff.isoformat() if cutoff else None
    record['eligibility_reasons'] = []
    if not cutoff:
        record['eligibility_reasons'].append('post_time_required')
    elif captured_jst >= cutoff:
        record['eligibility_reasons'].append('captured_after_post')
    if not record['same_day_measurements']:
        record['eligibility_reasons'].append('same_day_measurements_required')
    if not weather_valid:
        record['eligibility_reasons'].append('same_day_observed_weather_required')
    record['eligible_for_learning'] = bool(
        not record['missing_fields'] and not record['errors'] and record['same_day_measurements']
        and cutoff and captured_jst < cutoff and weather_valid
    )
    record['status'] = 'COMPLETE' if record['eligible_for_learning'] else 'INCOMPLETE'
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{captured.strftime('%Y%m%dT%H%M%S%f')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    record['observation_path'] = str(path)
    history = [dict(row, target_weather=weather, target_track_condition=condition,
                    target_conditions_captured_at=record['weather_captured_at'])
               for row in rows if row.get('race_id') == race_id and row.get('date')]
    if record['eligible_for_learning'] and history:
        cases = root / 'cases'
        cases.mkdir(exist_ok=True)
        (cases / path.name).write_text(json.dumps({'data_collector': {
            'rows': history, 'environment_snapshots': [record],
        }}, ensure_ascii=False, indent=2) + '\n')
    return record
