#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "track_biases.json"
REQUIRED_STYLE_KEYS = ("front", "stalker", "midpack", "closer")
REQUIRED_FRAME_KEYS = tuple(str(index) for index in range(1, 9))


class ValidationError(ValueError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and update track-bias priors.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--merge", type=Path, help="JSON file containing sources/profiles to merge.")
    parser.add_argument("--set-version", default="", help="Override version. Defaults to today when --merge is used.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the merged config without writing.")
    args = parser.parse_args()

    config = _load_json(args.config)
    validate_config(config)

    changed = False
    if args.merge:
        patch = _load_json(args.merge)
        validate_patch(patch)
        config = merge_config(config, patch)
        config["version"] = args.set_version or date.today().isoformat()
        validate_config(config)
        changed = True
    elif args.set_version:
        config["version"] = args.set_version
        validate_config(config)
        changed = True

    if args.dry_run:
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return 0

    if changed:
        args.config.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(
        f"track_biases ok: {len(config.get('profiles', []))} profiles, "
        f"{len(config.get('sources', []))} sources"
    )
    return 0


def merge_config(config: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(config, ensure_ascii=False))

    source_by_id = {str(source["id"]): source for source in merged.get("sources", [])}
    for source in patch.get("sources", []):
        source_by_id[str(source["id"])] = source
    merged["sources"] = sorted(source_by_id.values(), key=lambda item: str(item["id"]))

    profile_by_key = {_profile_key(profile): profile for profile in merged.get("profiles", [])}
    for profile in patch.get("profiles", []):
        profile_by_key[_profile_key(profile)] = profile
    merged["profiles"] = sorted(
        profile_by_key.values(),
        key=lambda item: (str(item["track"]), str(item["surface"]), int(item["distance"])),
    )

    return merged


def validate_patch(patch: dict[str, Any]) -> None:
    if not isinstance(patch, dict):
        raise ValidationError("patch must be an object")
    if "sources" in patch:
        _validate_sources(patch["sources"])
    if "profiles" in patch:
        _validate_profiles(patch["profiles"], source_ids={str(source["id"]) for source in patch.get("sources", [])})
    if not patch.get("sources") and not patch.get("profiles"):
        raise ValidationError("patch must include sources or profiles")


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValidationError("config must be an object")
    if not str(config.get("version", "")).strip():
        raise ValidationError("version is required")

    sources = config.get("sources", [])
    profiles = config.get("profiles", [])
    source_ids = _validate_sources(sources)
    _validate_profiles(profiles, source_ids=source_ids)


def _validate_sources(sources: Any) -> set[str]:
    if not isinstance(sources, list) or not sources:
        raise ValidationError("sources must be a non-empty list")
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValidationError("source entries must be objects")
        source_id = str(source.get("id", "")).strip()
        if not source_id:
            raise ValidationError("source id is required")
        if source_id in seen:
            raise ValidationError(f"duplicate source id: {source_id}")
        if not str(source.get("url", "")).startswith(("http://", "https://")):
            raise ValidationError(f"source url must be http(s): {source_id}")
        if not str(source.get("notes", "")).strip():
            raise ValidationError(f"source notes are required: {source_id}")
        seen.add(source_id)
    return seen


def _validate_profiles(profiles: Any, *, source_ids: set[str]) -> None:
    if not isinstance(profiles, list) or not profiles:
        raise ValidationError("profiles must be a non-empty list")
    seen: set[tuple[str, str, int]] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValidationError("profile entries must be objects")

        key = _profile_key(profile)
        if key in seen:
            raise ValidationError(f"duplicate profile: {key}")
        seen.add(key)

        strength = _number(profile.get("bias_strength"), f"{key} bias_strength")
        if not 0.0 <= strength <= 1.0:
            raise ValidationError(f"{key} bias_strength must be between 0 and 1")

        _validate_numeric_map(profile.get("running_style_bias"), REQUIRED_STYLE_KEYS, -1.0, 1.0, f"{key} running_style_bias")
        _validate_numeric_map(profile.get("frame_bias"), REQUIRED_FRAME_KEYS, -0.5, 0.5, f"{key} frame_bias")

        condition_adjustments = profile.get("condition_adjustments")
        if not isinstance(condition_adjustments, dict):
            raise ValidationError(f"{key} condition_adjustments must be an object")
        for condition, values in condition_adjustments.items():
            if condition not in {"良", "稍重", "重", "不良"}:
                raise ValidationError(f"{key} unknown condition: {condition}")
            _validate_numeric_map(values, REQUIRED_STYLE_KEYS, -0.5, 0.5, f"{key} condition {condition}")

        profile_source_ids = profile.get("source_ids")
        if not isinstance(profile_source_ids, list) or not profile_source_ids:
            raise ValidationError(f"{key} source_ids must be a non-empty list")
        missing = [source_id for source_id in profile_source_ids if source_ids and source_id not in source_ids]
        if missing:
            raise ValidationError(f"{key} references unknown source ids: {missing}")


def _validate_numeric_map(values: Any, keys: tuple[str, ...], minimum: float, maximum: float, label: str) -> None:
    if not isinstance(values, dict):
        raise ValidationError(f"{label} must be an object")
    missing = [key for key in keys if key not in values]
    if missing:
        raise ValidationError(f"{label} missing keys: {missing}")
    for key in keys:
        value = _number(values[key], f"{label}.{key}")
        if not minimum <= value <= maximum:
            raise ValidationError(f"{label}.{key} must be between {minimum} and {maximum}")


def _profile_key(profile: dict[str, Any]) -> tuple[str, str, int]:
    track = str(profile.get("track", "")).strip()
    surface = str(profile.get("surface", "")).strip()
    distance = int(_number(profile.get("distance"), f"{track}/{surface} distance"))
    if not track or not surface:
        raise ValidationError("profile track and surface are required")
    if distance <= 0:
        raise ValidationError(f"{track}/{surface} distance must be positive")
    return track, surface, distance


def _number(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be numeric") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except FileNotFoundError as exc:
        raise ValidationError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"top-level JSON must be an object: {path}")
    return data


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
