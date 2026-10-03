from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from src.deadline import JST
from scripts.run_final_prediction_windows import prediction_windows, run_due_windows


def _config(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "race_id": "20261004_中山_11",
                    "race_name": "test",
                    "race_date": "2026-10-04",
                    "track": "中山",
                    "race_number": 11,
                    "source_url": "https://example.test/race",
                    "post_time": "15:40",
                }
            ]
        ),
        encoding="utf-8",
    )


def _runner(calls: list[list[str]]):
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"decision":"NO_GO"}\n', "")

    return run


def test_prediction_windows_reserve_time_before_t5(tmp_path: Path) -> None:
    config_path = tmp_path / "race.json"
    _config(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))[0]

    windows = dict(prediction_windows(config))

    assert windows["T-15"] == datetime(2026, 10, 4, 15, 25, tzinfo=JST)
    assert windows["T-5_FINAL"] == datetime(2026, 10, 4, 15, 34, tzinfo=JST)


def test_due_window_runs_once_and_persists_state(tmp_path: Path) -> None:
    config_path = tmp_path / "race.json"
    state_path = tmp_path / "state.json"
    _config(config_path)
    calls: list[list[str]] = []
    kwargs = {
        "config_path": config_path,
        "baseline_path": None,
        "output_root": tmp_path / "output",
        "state_path": state_path,
        "now": datetime(2026, 10, 4, 15, 25, 30, tzinfo=JST),
        "grace_seconds": 90,
        "cutoff_minutes": 5,
        "minimum_refresh_seconds": 40,
        "emit_reserve_seconds": 10,
        "bankroll_per_race": 1000,
        "min_ev": 1.03,
        "mode": "balanced",
        "command_runner": _runner(calls),
    }

    first = run_due_windows(**kwargs)
    second = run_due_windows(**kwargs)

    assert first["status"] == "OK"
    assert second["status"] == "NO_ACTION"
    assert len(calls) == 1
    assert "T-15" in json.loads(state_path.read_text(encoding="utf-8"))["completed_windows"]
