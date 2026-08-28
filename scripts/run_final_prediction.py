#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jra_scraper.config import ScrapeConfig
from src.agents import WorkflowSettings
from src.artifacts import atomic_write_json as atomic_json
from src.deadline import DeadlineSettings, build_deadline_plan
from src.final_workflow import FinalPredictionWorkflow
from src.react_workflow import validate_canonical_stage_manifest


class HardOutputDeadlineExceeded(BaseException):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh JRA live data and emit a reviewed GO/NO_GO decision before T-5"
    )
    parser.add_argument("--config-path", required=True, help="One-race JSON config")
    parser.add_argument("--baseline-path", default="", help="pipeline_run.json or race_last5.csv")
    parser.add_argument("--output-root", default=str(ROOT / "report/final_predictions"))
    parser.add_argument("--cutoff-minutes", type=int, default=5)
    parser.add_argument("--normal-seconds", type=int, default=600)
    parser.add_argument("--emergency-seconds", type=int, default=120)
    parser.add_argument("--emit-reserve-seconds", type=int, default=10)
    parser.add_argument("--minimum-refresh-seconds", type=int, default=40)
    parser.add_argument("--odds-max-age-seconds", type=int, default=180)
    parser.add_argument("--conditions-max-age-seconds", type=int, default=300)
    parser.add_argument("--max-body-weight-change-kg", type=int, default=20)
    parser.add_argument("--request-timeout", type=int, default=5)
    parser.add_argument("--bankroll-per-race", type=int, default=1000)
    parser.add_argument("--min-ev", type=float, default=1.03)
    parser.add_argument("--mode", choices=["balanced", "aggressive"], default="balanced")
    args = parser.parse_args()

    race_config = load_single_race_config(Path(args.config_path))
    race_id = race_artifact_id(race_config)
    deadline_settings = DeadlineSettings(
        cutoff_minutes_before_post=args.cutoff_minutes,
        normal_seconds_before_cutoff=args.normal_seconds,
        emergency_seconds_before_cutoff=args.emergency_seconds,
        emit_reserve_seconds=args.emit_reserve_seconds,
        minimum_live_refresh_seconds=args.minimum_refresh_seconds,
        odds_max_age_seconds=args.odds_max_age_seconds,
        conditions_max_age_seconds=args.conditions_max_age_seconds,
        max_abs_body_weight_change_kg=args.max_body_weight_change_kg,
    )
    plan = build_deadline_plan(race_config, settings=deadline_settings)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    race_output_dir = Path(args.output_root) / race_id
    output_dir = race_output_dir / run_id
    baseline_path: Path | None = None
    try:
        with hard_output_deadline(plan.output_deadline):
            if plan.may_start_network_refresh:
                baseline_path = Path(args.baseline_path) if args.baseline_path else discover_baseline(race_id)
                try:
                    baseline = load_baseline(baseline_path) if baseline_path else {}
                except (OSError, csv.Error, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
                    baseline = {}
            else:
                baseline = {}
            live_config = build_live_config(output_dir, request_timeout=args.request_timeout)
            workflow = FinalPredictionWorkflow(
                live_config,
                output_dir=output_dir,
                workflow_settings=WorkflowSettings(
                    max_repair_attempts=0,
                    bankroll_per_race=args.bankroll_per_race,
                    min_ev=args.min_ev,
                    mode=args.mode,
                ),
                deadline_settings=deadline_settings,
            )
            payload = workflow.run(race_config, baseline=baseline)
            decision = dict(payload.get("final_decision") or {})
    except HardOutputDeadlineExceeded:
        decision = timeout_decision(race_config, plan)
        atomic_json(output_dir / "final_decision.json", decision)

    decision["artifact_dir"] = str(output_dir)
    decision["baseline_path"] = str(baseline_path) if baseline_path else ""
    atomic_json(output_dir / "final_decision.json", decision)
    atomic_json(race_output_dir / "latest_decision.json", decision)
    print(json.dumps(decision, ensure_ascii=False, separators=(",", ":")), flush=True)


def load_single_race_config(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError("final prediction config must contain exactly one race")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ValueError("race config must be a JSON object or one-item list")
    required = {"race_name", "race_date", "track", "race_number", "source_url", "post_time"}
    missing = sorted(key for key in required if not str(payload.get(key, "")).strip())
    if missing:
        raise ValueError(f"race config missing required fields: {missing}")
    return dict(payload)


def load_baseline(path: Path) -> dict[str, object] | list[dict[str, object]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as file_obj:
            return [dict(row) for row in csv.DictReader(file_obj)]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("data_collector"):
        manifest_errors = validate_canonical_stage_manifest(path.parent / "stages")
        payload["_baseline_manifest_valid"] = not manifest_errors
        payload["_baseline_manifest_errors"] = manifest_errors
    return payload


def discover_baseline(race_id: str) -> Path | None:
    direct_candidates = (
        ROOT / "report/races" / race_id / "pipeline_run.json",
        ROOT / "data/collected" / race_id / "race_last5.csv",
    )
    direct = next((path for path in direct_candidates if path.exists()), None)
    if direct:
        return direct

    # Some collection jobs use an ASCII directory slug even though the CSV
    # race_id is Japanese. Inspect only the first CSV row; never parse every
    # historical pipeline artifact on the latency-sensitive final path.
    return next(
        (
            path
            for path in sorted((ROOT / "data/collected").glob("*/race_last5.csv"))
            if csv_first_race_id(path) == race_id
        ),
        None,
    )


def csv_first_race_id(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", newline="") as file_obj:
            first = next(csv.DictReader(file_obj), {})
    except (OSError, csv.Error, UnicodeError):
        return ""
    return str(first.get("race_id", ""))


def race_artifact_id(config: dict[str, object]) -> str:
    configured = str(config.get("race_id", "")).strip()
    if configured:
        return configured
    date = str(config.get("race_date", "")).replace("-", "")
    track = str(config.get("track", "")).strip()
    number = int(float(str(config.get("race_number", "0"))))
    return f"{date}_{track}_{number:02d}"


def build_live_config(output_dir: Path, *, request_timeout: int) -> ScrapeConfig:
    return ScrapeConfig(
        output_csv=output_dir / "unused/race_last5.csv",
        entries_csv=output_dir / "unused/race_entries.csv",
        odds_snapshots_csv=output_dir / "unused/live_odds_snapshots.csv",
        combo_odds_csv=output_dir / "unused/live_combo_odds.csv",
        raw_dir=output_dir / "raw",
        state_path=output_dir / "unused/pipeline_state.json",
        quality_report_path=output_dir / "unused/data_quality.json",
        missing_history_requests_path=output_dir / "unused/missing_history_requests.json",
        manual_history_template_csv=output_dir / "unused/manual_history_template.csv",
        manual_history_csv=output_dir / "unused/manual_history.csv",
        stages_dir=output_dir,
        timeout=max(1, request_timeout),
        max_retries=1,
        delay_seconds=0.0,
    )


@contextmanager
def hard_output_deadline(deadline: datetime) -> Iterator[None]:
    if not hasattr(signal, "setitimer"):
        yield
        return
    deadline_utc = deadline.astimezone(timezone.utc)
    remaining = (deadline_utc - datetime.now(timezone.utc) - timedelta(seconds=0.75)).total_seconds()
    if remaining <= 0:
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_deadline(_signum, _frame) -> None:
        raise HardOutputDeadlineExceeded()

    signal.signal(signal.SIGALRM, _raise_deadline)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def timeout_decision(race_config: dict[str, object], plan) -> dict[str, object]:
    return {
        "status": "NG",
        "decision": "NO_GO",
        "decision_code": "NO_GO_HARD_DEADLINE",
        "race_name": str(race_config.get("race_name", "")),
        "post_time": plan.post_time.isoformat(),
        "output_deadline": plan.output_deadline.isoformat(),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": plan.execution_mode,
        "reason": "hard deadline watchdog stopped final processing before T-5",
        "tickets": [],
    }


if __name__ == "__main__":
    main()
