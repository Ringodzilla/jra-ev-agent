#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "course_learning.json"
STYLE_KEYS = ("front", "stalker", "midpack", "closer")


def update_from_review(config: dict[str, Any], review: dict[str, Any], ev_rows: list[dict[str, str]]) -> dict[str, Any]:
    if not ev_rows:
        return config

    race_meta = _race_meta(ev_rows)
    result = dict(review.get("result") or {})
    pace = str(result.get("pace", "")).strip()
    if pace and pace != "H":
        return config

    finish_numbers = [
        str(item.get("horse_number", "")).strip()
        for item in list(result.get("finish_order") or [])[:3]
        if str(item.get("horse_number", "")).strip()
    ]
    if not finish_numbers:
        return config

    field_style_counts = _style_counts(ev_rows)
    top3_style_counts = _style_counts([row for row in ev_rows if str(row.get("horse_number", "")).strip() in finish_numbers])
    if not field_style_counts or not top3_style_counts:
        return config

    field_total = sum(field_style_counts.values()) or 1
    top3_total = sum(top3_style_counts.values()) or 1
    learned_bias = {}
    for style in STYLE_KEYS:
        field_share = field_style_counts.get(style, 0) / field_total
        top3_share = top3_style_counts.get(style, 0) / top3_total
        learned_bias[style] = _clamp((top3_share - field_share) * 0.10, -0.08, 0.08)

    updated = json.loads(json.dumps(config, ensure_ascii=False))
    profiles = list(updated.get("pace_adjustments") or [])
    for scope, track in (
        ("surface_distance", ""),
        ("track_surface_distance", race_meta["track"]),
    ):
        profile_id = _profile_id(scope=scope, track=track, surface=race_meta["surface"], distance=race_meta["distance"])
        profiles = _upsert_profile(
            profiles,
            profile_id=profile_id,
            scope=scope,
            track=track,
            surface=race_meta["surface"],
            distance=race_meta["distance"],
            learned_bias=learned_bias,
        )

    updated["pace_adjustments"] = profiles
    return updated


def _upsert_profile(
    profiles: list[dict[str, Any]],
    *,
    profile_id: str,
    scope: str,
    track: str,
    surface: str,
    distance: int,
    learned_bias: dict[str, float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    replaced = False
    for profile in profiles:
        if str(profile.get("id", "")) != profile_id:
            out.append(profile)
            continue
        sample_size = int(_to_float(profile.get("sample_size"), 0.0))
        current_bias = {style: _to_float(dict(profile.get("style_bias") or {}).get(style), 0.0) for style in STYLE_KEYS}
        merged_bias = {
            style: _round(((current_bias[style] * sample_size) + learned_bias[style]) / (sample_size + 1))
            for style in STYLE_KEYS
        }
        updated = dict(profile)
        updated["style_bias"] = merged_bias
        updated["sample_size"] = sample_size + 1
        updated["confidence"] = _round(min(0.85, 0.20 + ((sample_size + 1) / 20.0)))
        out.append(updated)
        replaced = True

    if replaced:
        return out

    out.append(
        {
            "id": profile_id,
            "scope": scope,
            "track": track,
            "surface": surface,
            "distance": distance,
            "distance_tolerance": 1,
            "track_condition": "*",
            "high_pace_min": 0.3,
            "front_competitor_min": 2,
            "style_bias": {style: _round(learned_bias.get(style, 0.0)) for style in STYLE_KEYS},
            "closing_deficiency_penalty": {
                "threshold": 0.42,
                "style_bias": {"front": 0.0, "stalker": 0.0, "midpack": 0.0, "closer": 0.0},
            },
            "confidence": 0.25,
            "sample_size": 1,
            "enabled": True,
            "notes": "Auto-learned from race review labels.",
        }
    )
    return sorted(out, key=lambda item: str(item.get("id", "")))


def _race_meta(ev_rows: list[dict[str, str]]) -> dict[str, Any]:
    first = ev_rows[0]
    return {
        "track": str(first.get("target_track", "")).strip(),
        "surface": str(first.get("target_surface", "")).strip(),
        "distance": int(_to_float(first.get("target_distance"), 0.0)),
    }


def _style_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {style: 0 for style in STYLE_KEYS}
    for row in rows:
        counts[_running_style(_to_float(row.get("front_rate"), 0.5))] += 1
    return counts


def _running_style(front_rate: float) -> str:
    if front_rate >= 0.78:
        return "front"
    if front_rate >= 0.58:
        return "stalker"
    if front_rate >= 0.36:
        return "midpack"
    return "closer"


def _profile_id(*, scope: str, track: str, surface: str, distance: int) -> str:
    prefix = f"{track}_" if track else ""
    return f"learned_{prefix}{surface}{distance}_high_pace"


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    if not isinstance(data, dict):
        raise ValueError(f"top-level JSON must be an object: {path}")
    return data


def _round(value: float) -> float:
    return round(value, 4)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description="Update course-learning pace profiles from one race review.")
    parser.add_argument("--review-json", required=True)
    parser.add_argument("--ev-csv", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = _load_json(args.config)
    review = _load_json(Path(args.review_json))
    ev_rows = _load_csv(Path(args.ev_csv))
    updated = update_from_review(config, review, ev_rows)

    if args.dry_run:
        print(json.dumps(updated, ensure_ascii=False, indent=2))
        return 0

    args.config.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"config": str(args.config), "profiles": len(updated.get("pace_adjustments") or [])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
