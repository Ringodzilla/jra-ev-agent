import importlib.util
import json
from pathlib import Path

from src.track_bias import course_pace_adjustment


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "update_track_biases.py"
COURSE_SCRIPT_PATH = ROOT / "scripts" / "update_course_learning.py"

spec = importlib.util.spec_from_file_location("update_track_biases", SCRIPT_PATH)
update_track_biases = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(update_track_biases)

course_spec = importlib.util.spec_from_file_location("update_course_learning", COURSE_SCRIPT_PATH)
update_course_learning = importlib.util.module_from_spec(course_spec)
assert course_spec.loader is not None
course_spec.loader.exec_module(update_course_learning)


def test_current_track_bias_config_is_valid():
    config = json.loads((ROOT / "config" / "track_biases.json").read_text(encoding="utf-8"))

    update_track_biases.validate_config(config)


def test_course_learning_surface_distance_scope_can_apply_across_tracks():
    learned = {
        "pace_adjustments": [
            {
                "id": "turf1200_global",
                "scope": "surface_distance",
                "track": "",
                "surface": "芝",
                "distance": 1200,
                "distance_tolerance": 1,
                "track_condition": "*",
                "high_pace_min": 0.45,
                "front_competitor_min": 2,
                "style_bias": {"front": -0.10, "stalker": -0.02, "midpack": 0.04, "closer": 0.02},
                "closing_deficiency_penalty": {
                    "threshold": 0.42,
                    "style_bias": {"front": -0.05, "stalker": -0.02, "midpack": 0.0, "closer": 0.0},
                },
                "confidence": 0.80,
                "sample_size": 24,
                "enabled": True,
            }
        ]
    }

    adjustment = course_pace_adjustment(
        track="函館",
        surface="芝",
        distance=1200,
        track_condition="重",
        pace_mix_high=0.50,
        front_rate=0.90,
        closing_strength=0.20,
        front_competitor_count=3,
        learning_config=learned,
    )

    assert adjustment["course_pace_scope"] == "surface_distance"
    assert adjustment["course_pace_style"] == "front"
    assert float(adjustment["course_pace_adjustment"]) < 0


def test_update_course_learning_adds_surface_and_track_scoped_profiles():
    config = {"version": "test", "pace_adjustments": []}
    review = {
        "result": {
            "pace": "H",
            "finish_order": [
                {"horse_number": "5"},
                {"horse_number": "9"},
                {"horse_number": "2"},
            ],
        }
    }
    ev_rows = [
        {"horse_number": "5", "target_track": "函館", "target_surface": "芝", "target_distance": "1200", "front_rate": "1.0"},
        {"horse_number": "9", "target_track": "函館", "target_surface": "芝", "target_distance": "1200", "front_rate": "0.88"},
        {"horse_number": "2", "target_track": "函館", "target_surface": "芝", "target_distance": "1200", "front_rate": "0.90"},
        {"horse_number": "10", "target_track": "函館", "target_surface": "芝", "target_distance": "1200", "front_rate": "1.0"},
        {"horse_number": "11", "target_track": "函館", "target_surface": "芝", "target_distance": "1200", "front_rate": "1.0"},
        {"horse_number": "6", "target_track": "函館", "target_surface": "芝", "target_distance": "1200", "front_rate": "0.30"},
    ]

    updated = update_course_learning.update_from_review(config, review, ev_rows)
    ids = {profile["id"] for profile in updated["pace_adjustments"]}

    assert "learned_芝1200_high_pace" in ids
    assert "learned_函館_芝1200_high_pace" in ids
    assert all(profile["sample_size"] == 1 for profile in updated["pace_adjustments"])


def test_merge_replaces_matching_profile_and_adds_source():
    config = json.loads((ROOT / "config" / "track_biases.json").read_text(encoding="utf-8"))
    patch = {
        "sources": [
            {
                "id": "test_refresh",
                "url": "https://example.com/track-bias",
                "notes": "Synthetic refresh source for merge tests.",
            }
        ],
        "profiles": [
            {
                "track": "小倉",
                "surface": "ダート",
                "distance": 1700,
                "bias_strength": 0.19,
                "running_style_bias": {
                    "front": 0.21,
                    "stalker": 0.29,
                    "midpack": -0.12,
                    "closer": -0.39,
                },
                "frame_bias": {
                    "1": 0.0,
                    "2": 0.02,
                    "3": -0.02,
                    "4": 0.0,
                    "5": 0.02,
                    "6": 0.03,
                    "7": 0.01,
                    "8": 0.0,
                },
                "condition_adjustments": {
                    "良": {"front": 0.0, "stalker": 0.0, "midpack": 0.0, "closer": 0.0},
                    "稍重": {"front": 0.03, "stalker": 0.02, "midpack": -0.01, "closer": -0.03},
                    "重": {"front": 0.04, "stalker": 0.02, "midpack": -0.02, "closer": -0.04},
                    "不良": {"front": 0.04, "stalker": 0.03, "midpack": -0.02, "closer": -0.05},
                },
                "source_ids": ["test_refresh"],
            }
        ],
    }

    merged = update_track_biases.merge_config(config, patch)
    update_track_biases.validate_config(merged)

    profile = next(
        item
        for item in merged["profiles"]
        if item["track"] == "小倉" and item["surface"] == "ダート" and item["distance"] == 1700
    )
    source = next(item for item in merged["sources"] if item["id"] == "test_refresh")

    assert profile["bias_strength"] == 0.19
    assert profile["source_ids"] == ["test_refresh"]
    assert source["url"] == "https://example.com/track-bias"
