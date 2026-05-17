from __future__ import annotations

import csv
from collections import defaultdict

from src.feature_engineering import build_feature_row, summarize_history_rows


def build_features(race_last5_path: str, entries_path: str, odds_path: str) -> list[dict]:
    with open(race_last5_path, encoding="utf-8") as file_obj:
        history_rows = list(csv.DictReader(file_obj))

    summaries = summarize_history_rows(history_rows, group_keys=("horse_id",))

    odds_map: dict[tuple[str, str], str] = {}
    with open(odds_path, encoding="utf-8") as file_obj:
        for row in csv.DictReader(file_obj):
            odds_map[(str(row["race_id"]), str(row["horse_id"]))] = str(row.get("win_odds", ""))

    feature_rows: list[dict] = []
    with open(entries_path, encoding="utf-8") as file_obj:
        for row in csv.DictReader(file_obj):
            horse_id = str(row["horse_id"])
            current = {
                "race_id": str(row["race_id"]),
                "horse_id": horse_id,
                "horse_name": str(row.get("horse_name", "")),
                "horse_number": str(row.get("horse_number", "")),
                "assigned_weight": str(row.get("assigned_weight", "")),
                "current_odds": odds_map.get((str(row["race_id"]), horse_id), ""),
                "current_popularity": str(row.get("current_popularity", "")),
                "current_jockey": str(row.get("current_jockey", "")),
                "target_track": str(row.get("target_track", "")),
                "target_race_date": str(row.get("target_race_date", "")),
                "target_surface": str(row.get("target_surface", "")),
                "target_distance": str(row.get("target_distance", "")),
                "horse_country": str(row.get("horse_country", "")),
            }
            summary = summaries.get((horse_id,), {})
            feature_rows.append(build_feature_row(current, summary))

    by_race: dict[str, list[dict]] = defaultdict(list)
    for row in feature_rows:
        by_race[str(row["race_id"])].append(row)

    out: list[dict] = []
    for race_id in sorted(by_race.keys()):
        out.extend(sorted(by_race[race_id], key=lambda row: int(str(row.get("horse_number") or 999))))
    return out
