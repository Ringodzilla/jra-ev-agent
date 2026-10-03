from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PREFERRED_FIELDS = (
    "row_id",
    "race_id",
    "horse_id",
    "horse_name",
    "frame_number",
    "horse_number",
    "current_jockey",
    "assigned_weight",
    "current_body_weight",
    "body_weight_change",
    "body_weight_status",
    "current_odds",
    "current_popularity",
    "target_track",
    "target_race_date",
    "target_race_number",
    "target_surface",
    "target_distance",
    "target_weather",
    "target_track_condition",
    "target_conditions_captured_at",
    "horse_country",
    "run_index",
    "date",
    "race_name",
    "course",
    "distance",
    "history_surface",
    "position",
    "time",
    "weight",
    "jockey",
    "pace",
    "last_3f",
    "track_condition",
    "weather",
    "passing_order",
    "odds",
    "popularity",
)
LEAKAGE_KEY = re.compile(r"(^|_)(result|payout|future)(_|$)", re.IGNORECASE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_date(value: object, *, field: str, context: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{context}: {field} is empty")
    normalized = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )
    normalized = re.sub(r"\s.*$", "", normalized)
    parts = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if parts:
        normalized = f"{int(parts.group(1)):04d}-{int(parts.group(2)):02d}-{int(parts.group(3)):02d}"
    try:
        return date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{context}: invalid {field}={text!r}") from exc


def _load_labeled_race_ids(results_path: Path) -> set[str]:
    with results_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        rows = csv.DictReader(file_obj)
        if not rows.fieldnames or "race_id" not in rows.fieldnames:
            raise ValueError(f"{results_path}: race_id column is required")
        return {
            str(row.get("race_id") or "").strip()
            for row in rows
            if str(row.get("race_id") or "").strip()
            and str(row.get("race_id") or "").strip().upper() != "WIN5"
        }


def _validate_row(row: dict[str, Any], race_id: str, source: Path) -> None:
    context = f"{source}:{row.get('row_id') or row.get('horse_name') or 'row'}"
    if str(row.get("race_id") or "").strip() != race_id:
        raise ValueError(f"{context}: race_id does not match artifact directory {race_id}")
    suspect_keys = sorted(key for key in row if LEAKAGE_KEY.search(str(key)))
    if suspect_keys:
        raise ValueError(f"{context}: leakage-prone fields found: {', '.join(suspect_keys)}")

    target_date = _parse_date(row.get("target_race_date"), field="target_race_date", context=context)
    history_date = _parse_date(row.get("date"), field="date", context=context)
    if history_date >= target_date:
        raise ValueError(
            f"{context}: history date {history_date.isoformat()} is not before target date "
            f"{target_date.isoformat()}"
        )


def _sort_number(value: object) -> tuple[int, str]:
    text = str(value or "").strip()
    match = re.search(r"\d+", text)
    return (int(match.group()) if match else 10**9, text)


def build_fixed_dataset(
    *,
    artifacts_root: Path,
    results_path: Path,
    output_path: Path,
    manifest_path: Path,
    output_results_path: Path | None = None,
) -> dict[str, Any]:
    labeled_races = _load_labeled_race_ids(results_path)
    selected_rows: list[dict[str, Any]] = []
    source_files: list[dict[str, str]] = []
    selected_races: list[str] = []
    missing_races: list[str] = []
    seen: dict[str, dict[str, Any]] = {}

    for race_id in sorted(labeled_races):
        source = artifacts_root / race_id / "pipeline_run.json"
        if not source.exists():
            missing_races.append(race_id)
            continue
        payload = json.loads(source.read_text(encoding="utf-8"))
        rows = payload.get("data_collector", {}).get("rows", [])
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{source}: data_collector.rows must be a non-empty list")
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                raise ValueError(f"{source}: every collected row must be an object")
            row = dict(raw_row)
            _validate_row(row, race_id, source)
            dedupe_key = str(row.get("row_id") or "").strip() or "|".join(
                str(row.get(key) or "") for key in ("race_id", "horse_id", "horse_number", "run_index", "date")
            )
            if dedupe_key in seen and seen[dedupe_key] != row:
                raise ValueError(f"{source}: conflicting duplicate row {dedupe_key}")
            if dedupe_key not in seen:
                seen[dedupe_key] = row
                selected_rows.append(row)
        selected_races.append(race_id)
        source_files.append({"path": str(source), "sha256": _sha256(source)})

    if not selected_rows:
        raise ValueError("no labeled race artifacts were available")

    selected_rows.sort(
        key=lambda row: (
            str(row.get("race_id") or ""),
            _sort_number(row.get("horse_number")),
            _sort_number(row.get("run_index")),
            str(row.get("row_id") or ""),
        )
    )
    all_fields = {str(key) for row in selected_rows for key in row}
    fieldnames = [field for field in PREFERRED_FIELDS if field in all_fields]
    fieldnames.extend(sorted(all_fields - set(fieldnames)))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(selected_rows)

    fixed_results: dict[str, Any] | None = None
    if output_results_path is not None:
        with results_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
            reader = csv.DictReader(file_obj)
            result_fieldnames = list(reader.fieldnames or [])
            result_rows = [
                row for row in reader if str(row.get("race_id") or "").strip() in set(selected_races)
            ]
        output_results_path.parent.mkdir(parents=True, exist_ok=True)
        with output_results_path.open("w", encoding="utf-8", newline="") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=result_fieldnames)
            writer.writeheader()
            writer.writerows(result_rows)
        fixed_results = {
            "path": str(output_results_path),
            "sha256": _sha256(output_results_path),
            "row_count": len(result_rows),
        }

    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts_root": str(artifacts_root),
        "results": {"path": str(results_path), "sha256": _sha256(results_path)},
        "output": {"path": str(output_path), "sha256": _sha256(output_path)},
        "output_results": fixed_results,
        "source_files": source_files,
        "selected_races": selected_races,
        "labeled_races_missing_artifacts": missing_races,
        "race_count": len(selected_races),
        "row_count": len(selected_rows),
        "leakage_checks": {
            "history_strictly_before_target": True,
            "result_payout_future_fields_absent": True,
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a fixed, result-labeled evaluation dataset from race artifacts.")
    parser.add_argument("--artifacts-root", default="report/races")
    parser.add_argument("--results", default="data/processed/result_labels.csv")
    parser.add_argument("--output", default="data/evaluation/fixed_race_last5.csv")
    parser.add_argument("--output-results", default="data/evaluation/fixed_result_labels.csv")
    parser.add_argument("--manifest", default="data/evaluation/fixed_race_last5.manifest.json")
    args = parser.parse_args()

    manifest = build_fixed_dataset(
        artifacts_root=Path(args.artifacts_root),
        results_path=Path(args.results),
        output_path=Path(args.output),
        output_results_path=Path(args.output_results),
        manifest_path=Path(args.manifest),
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
