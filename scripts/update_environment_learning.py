#!/usr/bin/env python3
"""Accumulate pre-race observations, join labels, and retrain a versioned candidate."""
from __future__ import annotations

import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.environment_model import train_and_compare
from scripts.train_environment_model import build_training_races


def usable_snapshot(snapshot: dict) -> bool:
    if snapshot.get('eligible_for_learning') is not True or snapshot.get('value_type') != 'observed':
        return False
    try:
        captured = datetime.fromisoformat(snapshot['captured_at'])
        cutoff = datetime.fromisoformat(snapshot['cutoff_at'])
        stamps = [datetime.fromisoformat(snapshot[key]) for key in
                  ('cushion_measured_at', 'moisture_measured_at', 'weather_captured_at')]
        return (captured.tzinfo is not None and cutoff.tzinfo is not None and captured < cutoff
                and all(stamp.tzinfo is not None and stamp <= captured
                        and stamp.astimezone(timezone(timedelta(hours=9))).date().isoformat() == snapshot['date'] for stamp in stamps))
    except (KeyError, TypeError, ValueError):
        return False


def update_learning(root: Path = ROOT, labels_path: Path | None = None) -> dict:
    labels_path = labels_path or root / 'data/processed/result_labels.csv'
    seed = root / 'data/evaluation/fixed_race_last5.csv'
    seed_labels = root / 'data/evaluation/fixed_result_labels.csv'
    curated = root / 'data/environment/official_measurements.json'
    rows = list(csv.DictReader(seed.open())) if seed.exists() else []
    labels = list(csv.DictReader(seed_labels.open())) if seed_labels.exists() else []
    if labels_path.exists():
        labels += list(csv.DictReader(labels_path.open()))
    label_by_key = {}
    for label in labels:
        key = tuple(label.get(k, '') for k in ('race_id', '式別', '組番', '馬番'))
        if key in label_by_key and label_by_key[key] != label:
            raise ValueError(f'Conflicting outcome label: {key}')
        label_by_key[key] = label
    measurements = json.loads(curated.read_text())['records'] if curated.exists() else []
    sources = []
    pending = []
    known_ids = {row['race_id'] for row in rows}
    cases = sorted((root / 'report/environment_observations').glob('*/cases/*.json'), reverse=True)
    artifacts = sorted((root / 'report/races').glob('*/pipeline_run.json'))
    for path in cases + artifacts:
        payload = json.loads(path.read_text())
        collected = payload.get('data_collector', {})
        history = collected.get('rows', [])
        if not history:
            continue
        for race_id in sorted({row['race_id'] for row in history}):
            if race_id in known_ids:
                continue
            snapshots = [s for s in collected.get('environment_snapshots', [])
                         if s.get('race_id') == race_id and usable_snapshot(s)]
            if not snapshots:
                pending.append({'race_id': race_id, 'reason': 'missing_verified_pre_race_environment'})
                continue
            snapshot = max(snapshots, key=lambda s: s['captured_at'])
            rows += [dict(row, target_weather=snapshot['weather'],
                          target_track_condition=snapshot['track_condition'])
                     for row in history if row['race_id'] == race_id]
            measurements.append(snapshot)
            sources.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            known_ids.add(race_id)
    dataset = build_training_races(rows, list(label_by_key.values()), measurements)
    pending = list({p['race_id']: p for p in pending if p['race_id'] not in known_ids}.values())
    fingerprint = hashlib.sha256(json.dumps(dataset, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    code_sha256 = hashlib.sha256((ROOT / 'analysis/environment_model.py').read_bytes()).hexdigest()
    directory = root / 'data/learning/environment' / fingerprint / code_sha256
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + '\n')
    if len({r['date'] for r in dataset['races']}) >= 3:
        report = train_and_compare(dataset['races'])
    else:
        report = {'status': 'INSUFFICIENT_DATA', 'production_model_changed': False}
    report.update(dataset_sha256=fingerprint, captured_artifacts=sources,
                  model_code_sha256=code_sha256,
                  pending_environment=pending, excluded_races=dataset['excluded'],
                  generated_at=datetime.now(timezone.utc).isoformat())
    (directory / 'training_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    latest = root / 'report/environment_model/learning_status.json'
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(dict(report, run_directory=str(directory)), ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    report = update_learning()
    print(json.dumps({'status': report['status'], 'dataset_sha256': report['dataset_sha256']}, ensure_ascii=False))
