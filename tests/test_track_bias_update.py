import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "update_track_biases.py"

spec = importlib.util.spec_from_file_location("update_track_biases", SCRIPT_PATH)
update_track_biases = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(update_track_biases)


def test_current_track_bias_config_is_valid():
    config = json.loads((ROOT / "config" / "track_biases.json").read_text(encoding="utf-8"))

    update_track_biases.validate_config(config)


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
