from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_evaluation_integrity import (
    build_snapshot,
    create_manifest,
    parse_named,
    verify_manifest,
)


def test_integrity_manifest_detects_protected_file_change(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    baseline = tmp_path / "baseline.json"
    manifest = tmp_path / "integrity.json"
    source.write_text("original\n", encoding="utf-8")
    baseline.write_text('{"validation_roi": 1.0}\n', encoding="utf-8")
    files = {"input": str(source), "baseline": str(baseline)}
    parameters = {"min_ev": "1.05"}
    snapshot = build_snapshot(files, parameters)
    create_manifest(manifest, snapshot, replace=False)

    verify_manifest(manifest, build_snapshot(files, parameters))
    source.write_text("altered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="protected evaluation"):
        verify_manifest(manifest, build_snapshot(files, parameters))


def test_integrity_manifest_detects_baseline_and_manifest_tampering(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    manifest = tmp_path / "integrity.json"
    baseline.write_text('{"validation_roi": 1.0}\n', encoding="utf-8")
    files = {"baseline": str(baseline)}
    snapshot = build_snapshot(files, {})
    create_manifest(manifest, snapshot, replace=False)

    baseline.write_text('{"validation_roi": 2.0}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="protected evaluation"):
        verify_manifest(manifest, build_snapshot(files, {}))

    baseline.write_text('{"validation_roi": 1.0}\n', encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["snapshot"]["parameters"]["min_ev"] = "0.5"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest digest"):
        verify_manifest(manifest, build_snapshot(files, {}))


def test_integrity_named_values_reject_invalid_or_duplicate_names() -> None:
    with pytest.raises(ValueError, match="NAME=VALUE"):
        parse_named(["missing-separator"], option="--file")
    with pytest.raises(ValueError, match="duplicate"):
        parse_named(["input=a", "input=b"], option="--file")
