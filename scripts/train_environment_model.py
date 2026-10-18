#!/usr/bin/env python3
"""Fit and audit an opt-in environmental model; never replace production weights."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.environment_model import design_matrix, train_and_compare
from analysis.environment_scenarios import date_key
from analysis.ev import compute_ev


def build_training_races(history_rows: list[dict], labels: list[dict], measurements: list[dict]) -> dict:
    by_race = defaultdict(list)
    for row in history_rows:
        by_race[row['race_id']].append(row)
    observed = {}
    for measurement in measurements:
        key = (measurement['track'], date_key(measurement['date']))
        value_keys = ('cushion', 'turf_moisture_goal', 'turf_moisture_corner4',
                      'cushion_measured_at', 'moisture_measured_at')
        if key in observed and any(observed[key].get(field) != measurement.get(field) for field in value_keys):
            raise ValueError(f'Conflicting measurement: {key}')
        observed[key] = measurement
    winners = defaultdict(dict)
    for label in labels:
        if label['式別'] == '単勝':
            winners[label['race_id']][str(int(label['馬番']))] = float(label['払戻金'])
    races, excluded = [], []
    for race_id, rows in sorted(by_race.items()):
        first = rows[0]
        target_date = date_key(first['target_race_date'])
        if any(date_key(row['date']) >= target_date for row in rows):
            raise ValueError(f'Non-past history in {race_id}')
        if any(date_key(row['target_race_date']) != target_date or row['target_track'] != first['target_track'] for row in rows):
            raise ValueError(f'Conflicting target metadata: {race_id}')
        reason = ''
        if first['target_surface'] != '芝':
            reason = 'turf_cushion_not_applicable_to_dirt'
        elif not first['target_weather'] or not first['target_track_condition']:
            reason = 'missing_pre_race_weather_or_condition'
        elif (first['target_track'], target_date) not in observed:
            reason = 'missing_same_day_official_measurements'
        elif race_id not in winners:
            reason = 'missing_win_label'
        if reason:
            excluded.append({'race_id': race_id, 'reason': reason})
            continue
        environment = dict(observed[(first['target_track'], target_date)],
                           weather=first['target_weather'], track_condition=first['target_track_condition'])
        if not environment.get('source_url', '').startswith(('https://www.jra.go.jp/', 'https://jra.jp/')):
            raise ValueError(f'Official source required: {race_id}')
        # Morning measurements precede the afternoon fixed-evaluation races.
        for key in ('cushion_measured_at', 'moisture_measured_at'):
            stamp = datetime.fromisoformat(environment[key])
            if stamp.tzinfo is None or stamp.date().isoformat() != target_date or stamp.hour >= 12:
                raise ValueError(f'Unverified morning measurement timestamp: {race_id}')
        features = compute_ev(rows)
        total = sum(float(r['win_prob']) for r in features)
        for row in features:
            row['win_prob'] = float(row['win_prob']) / total
        design_matrix(features, environment)
        numbers = {str(int(row['horse_number'])) for row in features}
        if not set(winners[race_id]).issubset(numbers):
            raise ValueError(f'Winner missing from field: {race_id}')
        races.append({'race_id': race_id, 'date': target_date, 'rows': features,
                      'environment': environment, 'winners': list(winners[race_id]),
                      'win_payouts': winners[race_id]})
    return {'races': races, 'excluded': excluded}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'data/evaluation/fixed_race_last5.csv')
    parser.add_argument('--labels', type=Path, default=ROOT / 'data/evaluation/fixed_result_labels.csv')
    parser.add_argument('--measurements', type=Path, default=ROOT / 'data/environment/official_measurements.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'report/environment_model')
    args = parser.parse_args()
    with args.input.open() as file:
        history_rows = list(csv.DictReader(file))
    with args.labels.open() as file:
        labels = list(csv.DictReader(file))
    measurements = json.loads(args.measurements.read_text())['records']
    dataset = build_training_races(history_rows, labels, measurements)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + '\n')
    report = train_and_compare(dataset['races'])
    report['excluded_races'] = dataset['excluded']
    report['generated_at'] = datetime.now(timezone.utc).isoformat()
    report['source_files'] = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                              for path in (args.input, args.labels, args.measurements)]
    report['limitations'] = [
        'Small pilot dataset; minimum-sample thresholds are operational safeguards, not proof of generalization.',
        'Turf model only; no use of turf cushion measurements for dirt races.',
        'Morning measurements are not intraday forecasts. Pre-race weather timestamp precision still needs audit.',
        'Research splits and win-top1 ROI are separate from the unchanged fixed-evaluation production gates.',
    ]
    (args.output_dir / 'training_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (args.output_dir / 'candidate_model.json').write_text(json.dumps(report['selected_model'], ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'blocks', 'baseline', 'candidate', 'excluded_races')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
