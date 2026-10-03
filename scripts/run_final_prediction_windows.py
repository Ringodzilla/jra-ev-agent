#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.artifacts import atomic_write_json
from src.deadline import JST, race_post_datetime

from scripts.run_final_prediction import load_single_race_config, race_artifact_id


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def prediction_windows(
    race_config: dict[str, object],
    *,
    preview_minutes_before_post: int = 15,
    cutoff_minutes: int = 5,
    minimum_refresh_seconds: int = 40,
    emit_reserve_seconds: int = 10,
) -> list[tuple[str, datetime]]:
    post_time = race_post_datetime(race_config)
    final_safety_minutes = max(
        cutoff_minutes + 1,
        cutoff_minutes + math.ceil((minimum_refresh_seconds + emit_reserve_seconds) / 60),
    )
    return [
        ("T-15", post_time - timedelta(minutes=preview_minutes_before_post)),
        ("T-5_FINAL", post_time - timedelta(minutes=final_safety_minutes)),
    ]


def _load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"completed_windows": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("window state must be a JSON object")
    payload.setdefault("completed_windows", {})
    return payload


def run_due_windows(
    *,
    config_path: Path,
    baseline_path: Path | None,
    output_root: Path,
    state_path: Path,
    now: datetime,
    grace_seconds: int,
    cutoff_minutes: int,
    minimum_refresh_seconds: int,
    emit_reserve_seconds: int,
    bankroll_per_race: int,
    min_ev: float,
    mode: str,
    command_runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    race_config = load_single_race_config(config_path)
    race_id = race_artifact_id(race_config)
    state = _load_state(state_path)
    completed = dict(state.get("completed_windows") or {})
    now_jst = now.astimezone(JST) if now.tzinfo else now.replace(tzinfo=JST)
    windows = prediction_windows(
        race_config,
        cutoff_minutes=cutoff_minutes,
        minimum_refresh_seconds=minimum_refresh_seconds,
        emit_reserve_seconds=emit_reserve_seconds,
    )
    due = [
        (label, target)
        for label, target in windows
        if label not in completed and target <= now_jst <= target + timedelta(seconds=grace_seconds)
    ]
    if not due:
        return {
            "status": "NO_ACTION",
            "race_id": race_id,
            "evaluated_at": now_jst.isoformat(),
            "completed_windows": sorted(completed),
            "windows": {label: target.isoformat() for label, target in windows},
        }

    executed: list[dict[str, object]] = []
    for label, target in due:
        command = [
            sys.executable,
            str(ROOT / "scripts/run_final_prediction.py"),
            "--config-path",
            str(config_path),
            "--output-root",
            str(output_root),
            "--cutoff-minutes",
            str(cutoff_minutes),
            "--minimum-refresh-seconds",
            str(minimum_refresh_seconds),
            "--emit-reserve-seconds",
            str(emit_reserve_seconds),
            "--bankroll-per-race",
            str(bankroll_per_race),
            "--min-ev",
            str(min_ev),
            "--mode",
            mode,
        ]
        if baseline_path is not None:
            command.extend(["--baseline-path", str(baseline_path)])
        result = command_runner(command, check=False, capture_output=True, text=True)
        record = {
            "label": label,
            "scheduled_at": target.isoformat(),
            "executed_at": now_jst.isoformat(),
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
        executed.append(record)
        if result.returncode != 0:
            return {"status": "ERROR", "race_id": race_id, "executed": executed}
        completed[label] = record
        state.update(
            {
                "race_id": race_id,
                "config_path": str(config_path),
                "completed_windows": completed,
                "updated_at": now_jst.isoformat(),
            }
        )
        atomic_write_json(state_path, state)

    return {"status": "OK", "race_id": race_id, "executed": executed}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run idempotent T-15 and pre-T-5 final refreshes when invoked once per minute."
    )
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--baseline-path", default="")
    parser.add_argument("--output-root", default=str(ROOT / "report/final_predictions"))
    parser.add_argument("--state-path", default="")
    parser.add_argument("--grace-seconds", type=int, default=90)
    parser.add_argument("--cutoff-minutes", type=int, default=5)
    parser.add_argument("--minimum-refresh-seconds", type=int, default=40)
    parser.add_argument("--emit-reserve-seconds", type=int, default=10)
    parser.add_argument("--bankroll-per-race", type=int, default=1000)
    parser.add_argument("--min-ev", type=float, default=1.03)
    parser.add_argument("--mode", choices=("balanced", "aggressive"), default="balanced")
    args = parser.parse_args()

    config_path = Path(args.config_path)
    race_config = load_single_race_config(config_path)
    race_id = race_artifact_id(race_config)
    state_path = (
        Path(args.state_path)
        if args.state_path
        else Path(args.output_root) / race_id / "window_state.json"
    )
    payload = run_due_windows(
        config_path=config_path,
        baseline_path=Path(args.baseline_path) if args.baseline_path else None,
        output_root=Path(args.output_root),
        state_path=state_path,
        now=datetime.now(tz=JST),
        grace_seconds=max(0, args.grace_seconds),
        cutoff_minutes=args.cutoff_minutes,
        minimum_refresh_seconds=args.minimum_refresh_seconds,
        emit_reserve_seconds=args.emit_reserve_seconds,
        bankroll_per_race=args.bankroll_per_race,
        min_ev=args.min_ev,
        mode=args.mode,
    )
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if payload["status"] == "ERROR":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
