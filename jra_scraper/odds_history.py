"""Read completed, pre-race odds snapshots without changing prediction artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from analysis.candidate_ev import canonical_combination

_ODDS_FIELDS = {
    "race_id", "bet_type", "combination", "odds", "odds_min", "odds_max",
    "captured_at", "source_cname", "snapshot_id", "snapshot_complete",
    "snapshot_completed_at",
}
_BET_TYPES = {"win", "place", "wide", "wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan"}


def load_pre_race_odds_history(
    path: Path,
    *,
    race_id: str,
    as_of: datetime,
    post_time: datetime,
    prior_runs_dir: Path | None = None,
) -> list[dict[str, object]]:
    """Merge CSV history with this race's earlier final-run collector artifacts.

    Only the collector file is read; analysis and result files are never inputs.
    Conflicting observations at the same time are discarded instead of choosing
    a source by filesystem order. No shared CSV writes or locks are needed.
    """
    if as_of.tzinfo is None or post_time.tzinfo is None:
        raise ValueError("odds history cutoffs must include a timezone")
    rows = _csv_rows(path, race_id)
    if prior_runs_dir is not None:
        for collector in sorted(prior_runs_dir.glob("*/01_data_collector.json")):
            rows.extend(_completed_snapshot(collector, race_id, as_of, post_time))

    observations: dict[tuple[str, str, str], dict[str, object]] = {}
    conflicts: set[tuple[str, str, str]] = set()
    for row in rows:
        captured = _timestamp(row.get("captured_at"))
        if not _valid_row(row, race_id, as_of, post_time):
            continue
        combination = canonical_combination(str(row["bet_type"]), row["combination"])
        row = {**row, "combination": combination}
        key = (str(row["bet_type"]), combination, captured.isoformat())
        previous = observations.get(key)
        if previous is not None and _prices(previous) != _prices(row):
            conflicts.add(key)
        observations[key] = row
    return [observations[key] for key in sorted(observations) if key not in conflicts]


def _csv_rows(path: Path, race_id: str) -> list[dict[str, object]]:
    try:
        with path.open(encoding="utf-8", newline="") as file_obj:
            return [dict(row) for row in csv.DictReader(file_obj) if row.get("race_id") == race_id]
    except (OSError, csv.Error, UnicodeError):
        return []


def _completed_snapshot(
    path: Path, race_id: str, as_of: datetime, post_time: datetime,
) -> list[dict[str, object]]:
    try:
        raw = path.read_bytes()
        manifest = json.loads((path.parent / "run_manifest.json").read_text(encoding="utf-8"))
        expected = manifest["artifacts"][path.name]["sha256"]
        if hashlib.sha256(raw).hexdigest() != expected:
            return []
        snapshot = json.loads(raw)
        completed = _timestamp(snapshot["completed_at"])
        started = _timestamp(snapshot["started_at"])
        rows = snapshot["combo_odds"]
        snapshot_id = snapshot["snapshot_id"]
        if (
            snapshot["snapshot_complete"] is not True
            or snapshot["quality_report"]["combination_coverage"]["complete"] is not True
            or not snapshot_id
            or not isinstance(rows, list) or not rows
            or completed is None or started is None
            or not started <= completed <= as_of or completed >= post_time
        ):
            return []
        for row in rows:
            if (
                not isinstance(row, dict)
                or row.get("snapshot_id") != snapshot_id
                or row.get("snapshot_complete") is not True
                or not _valid_row(row, race_id, completed, post_time)
                or not started <= _timestamp(row.get("captured_at")) <= completed
            ):
                return []
        return rows
    except (OSError, ValueError, TypeError, KeyError):
        return []


def _valid_row(row: dict[str, object], race_id: str, as_of: datetime, post_time: datetime) -> bool:
    captured = _timestamp(row.get("captured_at"))
    if (
        set(row) - _ODDS_FIELDS
        or row.get("race_id") != race_id
        or row.get("bet_type") not in _BET_TYPES
        or not str(row.get("combination", "")).strip()
        or captured is None or captured > as_of or captured >= post_time
        or (row.get("snapshot_id") and "snapshot_complete" not in row)
        or ("snapshot_complete" in row and str(row["snapshot_complete"]).lower() not in {"true", "1"})
    ):
        return False
    if row.get("snapshot_completed_at"):
        completed = _timestamp(row["snapshot_completed_at"])
        if completed is None or not captured <= completed <= as_of or completed >= post_time:
            return False
    try:
        canonical_combination(str(row["bet_type"]), row["combination"])
        prices = _prices(row)
        return any(value > 0 for value in prices) and all(math.isfinite(value) and value >= 0 for value in prices)
    except (TypeError, ValueError):
        return False


def _prices(row: dict[str, object]) -> tuple[float, ...]:
    return tuple(float(row.get(key) or 0) for key in ("odds", "odds_min", "odds_max"))


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None
