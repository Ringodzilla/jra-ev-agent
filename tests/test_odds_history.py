from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone

import pytest

from jra_scraper.odds_history import load_pre_race_odds_history
from scripts.run_final_prediction import load_combo_odds_history


AS_OF = datetime(2026, 9, 12, 6, 36, tzinfo=timezone.utc)
POST = datetime(2026, 9, 12, 6, 45, tzinfo=timezone.utc)


def row(**overrides):
    return {
        'race_id': 'R1', 'bet_type': 'win', 'combination': '2',
        'odds': '13.8', 'captured_at': '2026-09-12T04:20:00Z', **overrides,
    }


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for item in rows for key in item))
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_snapshot(directory, name='early', rows=None, **overrides):
    folder = directory / name
    folder.mkdir(parents=True, exist_ok=True)
    rows = rows if rows is not None else [row(snapshot_id=name, snapshot_complete=True)]
    payload = {
        'snapshot_complete': True, 'snapshot_id': name,
        'started_at': '2026-09-12T04:19:59Z',
        'completed_at': '2026-09-12T04:20:01Z',
        'combo_odds': rows,
        'quality_report': {'combination_coverage': {'complete': True}},
        **overrides,
    }
    artifact = folder / '01_data_collector.json'
    artifact.write_text(json.dumps(payload))
    (folder / 'run_manifest.json').write_text(json.dumps({
        'artifacts': {artifact.name: {'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}}
    }))
    return artifact


def load(path, directory=None, **kwargs):
    return load_pre_race_odds_history(
        path, race_id='R1', as_of=kwargs.get('as_of', AS_OF),
        post_time=kwargs.get('post_time', POST), prior_runs_dir=directory,
    )


def test_successive_runs_preserve_intraday_history_and_deduplicate_csv(tmp_path):
    path = tmp_path / 'history.csv'
    earlier = row(odds='8.5', captured_at='2026-09-11T13:55:00Z')
    write_csv(path, [earlier, row(), row(race_id='R2')])
    directory = tmp_path / 'R1'
    first = write_snapshot(directory)
    assert len(load(path, directory)) == 2
    write_snapshot(directory, 'later', [row(
        odds='15.8', captured_at='2026-09-12T05:47:00Z',
        snapshot_id='later', snapshot_complete=True,
        snapshot_completed_at='2026-09-12T05:47:01Z',
    )], started_at='2026-09-12T05:46:59Z', completed_at='2026-09-12T05:47:01Z')
    # A previous NO_GO on betting does not invalidate a fully collected snapshot.
    (first.parent / 'final_decision.json').write_text('{"decision":"NO_GO"}')
    before = first.read_bytes()
    rows = load_combo_odds_history(path, race_id='R1', as_of=AS_OF, post_time=POST, prior_runs_dir=directory)
    assert [float(r['odds']) for r in rows] == [8.5, 13.8, 15.8]
    assert first.read_bytes() == before
    # Replay cutoff cannot see a later snapshot, even though it exists on disk.
    assert len(load(path, directory, as_of=datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc))) == 2


@pytest.mark.parametrize('override', [
    {'captured_at': 'broken'}, {'captured_at': '2026-09-12T04:20:00'},
    {'captured_at': '2026-09-12T06:40:00Z'},
    {'captured_at': '2026-09-12T06:45:00Z'},
    {'captured_at': '2026-09-12T06:46:00Z'},
    {'bet_type': 'unknown'}, {'combination': ''},
    {'odds': 'NaN'}, {'odds': 'Infinity'}, {'odds': '-10'}, {'odds': 'n/a'}, {'odds': '0'},
    {'snapshot_complete': False}, {'snapshot_id': 'incomplete'},
    {'snapshot_completed_at': 'bad'},
    {'snapshot_completed_at': '2026-09-12T04:19:00Z'},
    {'snapshot_completed_at': '2026-09-12T06:40:00Z'},
    {'snapshot_completed_at': '2026-09-12T06:45:00Z'},
    {'result': 'winner'},
])
def test_csv_rejects_invalid_incomplete_or_outcome_rows(tmp_path, override):
    path = tmp_path / 'history.csv'
    write_csv(path, [row(**override)])
    assert load(path) == []


def test_strict_post_cutoff_even_when_replaying_after_race(tmp_path):
    path = tmp_path / 'history.csv'
    write_csv(path, [row(captured_at='2026-09-12T06:45:00Z')])
    assert load(path, as_of=datetime(2026, 9, 12, 7, tzinfo=timezone.utc)) == []


@pytest.mark.parametrize('override', [
    {'snapshot_complete': False}, {'snapshot_id': ''},
    {'completed_at': 'bad'}, {'started_at': 'bad'},
    {'started_at': '2026-09-12T05:00:00Z'},
    {'completed_at': '2026-09-12T06:37:00Z'},
    {'completed_at': '2026-09-12T06:45:00Z'},
    {'quality_report': {'combination_coverage': {'complete': False}}},
    {'combo_odds': []}, {'combo_odds': {}}, {'combo_odds': ['bad']},
    {'combo_odds': [row(race_id='R2', snapshot_id='early', snapshot_complete=True)]},
    {'combo_odds': [row(snapshot_id='other', snapshot_complete=True)]},
    {'combo_odds': [row(snapshot_id='early', snapshot_complete=False)]},
    {'combo_odds': [row(snapshot_id='early', snapshot_complete=True, captured_at='2026-09-12T04:19:00Z')]},
    {'combo_odds': [row(snapshot_id='early', snapshot_complete=True, odds=[1])]},
])
def test_incomplete_or_incoherent_saved_snapshot_is_not_history(tmp_path, override):
    write_snapshot(tmp_path / 'runs', **override)
    assert load(tmp_path / 'missing.csv', tmp_path / 'runs') == []


def test_invalid_files_and_integrity_failures(tmp_path):
    missing = tmp_path / 'missing.csv'
    runs = tmp_path / 'runs'
    artifact = write_snapshot(runs)
    artifact.write_text('{}')  # changed since the manifest was committed
    assert load(missing, runs) == []
    artifact = write_snapshot(runs)
    (artifact.parent / 'run_manifest.json').write_text('[]')
    assert load(missing, runs) == []
    (artifact.parent / 'run_manifest.json').unlink()
    assert load(missing, runs) == []
    write_snapshot(runs)
    (artifact.parent / 'run_manifest.json').write_bytes(b'\xff')
    assert load(missing, runs) == []
    invalid_csv = tmp_path / 'invalid.csv'
    invalid_csv.write_bytes(b'\xff')
    assert load(invalid_csv) == []
    assert load(tmp_path) == []


def test_conflicting_duplicate_is_discarded_not_source_order_dependent(tmp_path):
    path = tmp_path / 'history.csv'
    write_csv(path, [row(odds='13.8'), row(odds='14.0'), row(odds='13.8')])
    assert load(path) == []
    write_csv(path, [row(odds='13.80'), row(odds='13.8', captured_at='2026-09-12T13:20:00+09:00')])
    assert len(load(path)) == 1


def test_cutoffs_require_timezone(tmp_path):
    with pytest.raises(ValueError, match='timezone'):
        load(tmp_path, as_of=datetime(2026, 9, 12))
    with pytest.raises(ValueError, match='timezone'):
        load(tmp_path, post_time=datetime(2026, 9, 12))


def test_legacy_and_range_odds_supported(tmp_path):
    path = tmp_path / 'history.csv'
    write_csv(path, [row(odds='', odds_min='2.2', odds_max='3.1', snapshot_complete='1')])
    assert len(load(path)) == 1


@pytest.mark.parametrize('before_deadline', [True, False])
def test_cli_supplies_prior_history_only_before_deadline(tmp_path, monkeypatch, capsys, before_deadline):
    import sys
    from contextlib import nullcontext
    from scripts import run_final_prediction as cli
    from src.deadline import build_deadline_plan

    config = {
        'race_id': 'R1', 'race_name': 'test', 'race_date': '2026-09-12',
        'track': '阪神', 'race_number': 11, 'post_time': '15:45',
        'source_url': 'https://example.invalid',
    }
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps([config]))
    csv_path = tmp_path / 'history.csv'
    write_csv(csv_path, [row(odds='8.5', captured_at='2026-09-11T13:55:00Z')])
    output = tmp_path / 'runs'
    write_snapshot(output / 'R1')
    plan = build_deadline_plan(config, now=AS_OF if before_deadline else POST)
    monkeypatch.setattr(cli, 'build_deadline_plan', lambda *a, **kw: plan)
    monkeypatch.setattr(cli, 'hard_output_deadline', lambda *a: nullcontext())
    monkeypatch.setattr(cli, 'discover_baseline', lambda *a: None)
    received = []

    class Workflow:
        def __init__(self, *a, **kw):
            pass

        def run(self, race_config, **kw):
            received.extend(kw['odds_history'])
            return {'final_decision': {'decision': 'NO_GO', 'tickets': []}}

    monkeypatch.setattr(cli, 'FinalPredictionWorkflow', Workflow)
    if not before_deadline:
        def forbidden_history_read(*a, **kw):
            pytest.fail('no history IO should start after the output deadline')
        monkeypatch.setattr(cli, 'load_combo_odds_history', forbidden_history_read)
    monkeypatch.setattr(sys, 'argv', [
        'run_final_prediction.py', '--config-path', str(config_path),
        '--odds-history-path', str(csv_path), '--output-root', str(output),
    ])
    cli.main()
    assert len(received) == (2 if before_deadline else 0)
    assert json.loads(capsys.readouterr().out)['decision'] == 'NO_GO'


@pytest.mark.parametrize('bet_type,left,right', [
    ('wide', '1-2', '2-1'), ('umaren', '1-2', '02-01'),
    ('win', '02', '2'), ('sanrenpuku', '3-2-1', '1-2-3'),
    ('wakuren', '2-2', '02-02'),
])
def test_combination_aliases_share_conflict_identity(tmp_path, bet_type, left, right):
    path = tmp_path / 'history.csv'
    write_csv(path, [row(bet_type=bet_type, combination=left), row(bet_type=bet_type, combination=right, odds='14')])
    assert load(path) == []
    write_csv(path, [row(bet_type=bet_type, combination=left), row(bet_type=bet_type, combination=right)])
    assert len(load(path)) == 1


def test_ordered_combinations_remain_distinct(tmp_path):
    path = tmp_path / 'history.csv'
    write_csv(path, [row(bet_type='umatan', combination='1-2'), row(bet_type='umatan', combination='2-1', odds='14')])
    assert {x['combination'] for x in load(path)} == {'1>2', '2>1'}


@pytest.mark.parametrize('combination', ['1-1', '0-2', '2', 'no-numbers'])
def test_invalid_combinations_are_not_history(tmp_path, combination):
    path = tmp_path / 'history.csv'
    write_csv(path, [row(bet_type='wide', combination=combination)])
    assert load(path) == []
