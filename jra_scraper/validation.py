from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime

OUTPUT_COLUMNS = [
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
]

ENTRY_COLUMNS = [
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
    "history_count",
]


def validate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in rows:
        normalized = _normalize_row(row)
        normalized["row_id"] = build_row_id(normalized)
        if normalized["row_id"] in seen:
            continue
        seen.add(normalized["row_id"])
        deduped.append(normalized)

    by_horse: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in deduped:
        by_horse[(row["race_id"], row["horse_id"] or row["horse_name"])].append(row)

    out: list[dict[str, str]] = []
    for key in sorted(by_horse.keys()):
        group_rows = by_horse[key]
        sorted_rows = sorted(group_rows, key=lambda r: _safe_int(r["run_index"]))
        out.extend(sorted_rows[:5])

    return out


def build_entry_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["race_id"], row["horse_id"] or row["horse_name"])].append(row)

    entries: list[dict[str, str]] = []
    for key in sorted(grouped.keys()):
        group_rows = sorted(grouped[key], key=lambda r: _safe_int(r["run_index"]))
        first = group_rows[0]
        entries.append(
            {
                "race_id": first["race_id"],
                "horse_id": first["horse_id"],
                "horse_name": first["horse_name"],
                "frame_number": first["frame_number"],
                "horse_number": first["horse_number"],
                "current_jockey": first["current_jockey"],
                "assigned_weight": first["assigned_weight"],
                "current_body_weight": first["current_body_weight"],
                "body_weight_change": first["body_weight_change"],
                "body_weight_status": first["body_weight_status"],
                "current_odds": first["current_odds"],
                "current_popularity": first["current_popularity"],
                "target_track": first["target_track"],
                "target_race_date": first["target_race_date"],
                "target_race_number": first["target_race_number"],
                "target_surface": first["target_surface"],
                "target_distance": first["target_distance"],
                "target_weather": first["target_weather"],
                "target_track_condition": first["target_track_condition"],
                "target_conditions_captured_at": first["target_conditions_captured_at"],
                "horse_country": first["horse_country"],
                "history_count": str(len(group_rows)),
            }
        )
    return entries


def build_row_id(row: dict[str, str]) -> str:
    stable_keys = ["race_id", "horse_id", "run_index", "date", "race_name", "position", "odds"]
    payload = "|".join(str(row.get(key, "")).strip() for key in stable_keys)
    return f"row_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:20]}"


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    data = {col: str(row.get(col, "")).strip() for col in OUTPUT_COLUMNS if col != "row_id"}

    data["horse_id"] = _normalize_horse_id(data["horse_id"], data["horse_name"])
    data["frame_number"] = _normalize_int(data["frame_number"])
    data["horse_number"] = _normalize_int(data["horse_number"])
    data["assigned_weight"] = _normalize_float(data["assigned_weight"])
    data["current_body_weight"] = _normalize_int(data["current_body_weight"])
    data["body_weight_change"] = _normalize_int(data["body_weight_change"])
    data["body_weight_status"] = _normalize_body_weight_status(
        data["body_weight_status"],
        data["current_body_weight"],
    )
    data["current_odds"] = _normalize_float(data["current_odds"])
    data["current_popularity"] = _normalize_int(data["current_popularity"])
    data["target_race_date"] = _normalize_date(data["target_race_date"])
    data["target_race_number"] = _normalize_int(data["target_race_number"])
    data["target_distance"] = _normalize_int(data["target_distance"])
    data["horse_country"] = _normalize_country_code(data["horse_country"])
    data["date"] = _normalize_date(data["date"])
    data["distance"] = _normalize_int(data["distance"])
    data["position"] = _normalize_int(data["position"])
    data["weight"] = _normalize_float(data["weight"])
    data["time"] = _normalize_time(data["time"])
    data["pace"] = _normalize_time(data["pace"])
    data["last_3f"] = _normalize_float(data["last_3f"])
    data["passing_order"] = _normalize_passing_order(data["passing_order"])
    data["odds"] = _normalize_float(data["odds"])
    data["popularity"] = _normalize_int(data["popularity"])

    return data


def _normalize_horse_id(raw_id: str, horse_name: str) -> str:
    if raw_id:
        return raw_id
    cleaned = re.sub(r"\s+", "_", horse_name.strip().lower())
    cleaned = re.sub(r"[^a-z0-9_\-ぁ-んァ-ヶ一-龠]", "", cleaned)
    return cleaned or "unknown_horse"


def _normalize_date(value: str) -> str:
    if not value:
        return ""
    value = value.replace(".", "/").replace("-", "/")
    for fmt in ("%Y/%m/%d", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def _normalize_int(value: str) -> str:
    match = re.search(r"-?\d+", value or "")
    return match.group(0) if match else ""


def _normalize_float(value: str) -> str:
    match = re.search(r"-?\d+(?:\.\d+)?", value or "")
    if not match:
        return ""
    return f"{float(match.group(0)):.1f}".rstrip("0").rstrip(".")


def _normalize_country_code(value: str) -> str:
    code = re.sub(r"[^A-Za-z]", "", value or "").upper()
    return code[:3] if code else ""


def _normalize_body_weight_status(value: str, current_body_weight: str) -> str:
    if current_body_weight:
        return "published"
    normalized = (value or "").strip().lower()
    if normalized in {"not_measured", "計不"}:
        return "not_measured"
    return "unpublished"


def _normalize_time(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if ":" in value:
        try:
            minute, sec = value.split(":", 1)
            total = int(minute) * 60 + float(sec)
            return f"{total:.1f}".rstrip("0").rstrip(".")
        except ValueError:
            return value
    return _normalize_float(value)


def _normalize_passing_order(value: str) -> str:
    if not value:
        return ""
    parts = [part for part in re.split(r"[-→]", value) if part.strip()]
    if not parts:
        return ""
    return _normalize_int(parts[-1])


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 999
