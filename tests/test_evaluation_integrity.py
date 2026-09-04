from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_evaluation_integrity import (
    build_snapshot,
    create_manifest,
    file_sha256,
    parse_named,
    verify_manifest,
)
from scripts.promote_evaluation_candidate import validate_and_maybe_promote


SIGNING_KEY = b"test-evaluation-integrity-key-32-bytes"


def test_integrity_manifest_detects_protected_file_change(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    baseline = tmp_path / "baseline.json"
    manifest = tmp_path / "integrity.json"
    source.write_text("original\n", encoding="utf-8")
    baseline.write_text('{"validation_roi": 1.0}\n', encoding="utf-8")
    files = {"input": str(source), "baseline": str(baseline)}
    parameters = {"min_ev": "1.05"}
    snapshot = build_snapshot(files, parameters)
    create_manifest(manifest, snapshot, key=SIGNING_KEY, replace=False)

    verify_manifest(manifest, build_snapshot(files, parameters), key=SIGNING_KEY)
    source.write_text("altered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="protected evaluation"):
        verify_manifest(manifest, build_snapshot(files, parameters), key=SIGNING_KEY)


def test_integrity_manifest_detects_baseline_and_manifest_tampering(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    manifest = tmp_path / "integrity.json"
    baseline.write_text('{"validation_roi": 1.0}\n', encoding="utf-8")
    files = {"baseline": str(baseline)}
    snapshot = build_snapshot(files, {})
    create_manifest(manifest, snapshot, key=SIGNING_KEY, replace=False)

    baseline.write_text('{"validation_roi": 2.0}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="protected evaluation"):
        verify_manifest(manifest, build_snapshot(files, {}), key=SIGNING_KEY)

    baseline.write_text('{"validation_roi": 1.0}\n', encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["snapshot"]["parameters"]["min_ev"] = "0.5"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="signature mismatch"):
        verify_manifest(manifest, build_snapshot(files, {}), key=SIGNING_KEY)


def test_integrity_manifest_requires_key_and_compare_and_swap_for_replacement(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.json"
    manifest = tmp_path / "integrity.json"
    baseline.write_text('{"validation_roi": 1.0}\n', encoding="utf-8")
    files = {"baseline": str(baseline)}
    create_manifest(manifest, build_snapshot(files, {}), key=SIGNING_KEY, replace=False)

    with pytest.raises(ValueError, match="signature mismatch"):
        verify_manifest(manifest, build_snapshot(files, {}), key=b"wrong-key-that-is-at-least-32-bytes")

    baseline.write_text('{"validation_roi": 2.0}\n', encoding="utf-8")
    updated = build_snapshot(files, {})
    with pytest.raises(ValueError, match="expected previous"):
        create_manifest(manifest, updated, key=SIGNING_KEY, replace=True)
    with pytest.raises(ValueError, match="changed before replacement"):
        create_manifest(
            manifest,
            updated,
            key=SIGNING_KEY,
            replace=True,
            expected_previous_sha256="0" * 64,
        )

    previous_sha256 = file_sha256(manifest)
    create_manifest(
        manifest,
        updated,
        key=SIGNING_KEY,
        replace=True,
        expected_previous_sha256=previous_sha256,
    )
    verify_manifest(manifest, updated, key=SIGNING_KEY)


def test_candidate_promotion_uses_the_exact_validated_bytes(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    baseline = tmp_path / "baseline.json"
    candidate_bytes = b'{"label_status":"available","result_label_count":1,"score":2}\n'
    candidate.write_bytes(candidate_bytes)
    baseline.write_text('{"score":1}\n', encoding="utf-8")
    metrics = json.loads(candidate_bytes)

    decision = validate_and_maybe_promote(
        {"metrics": metrics, "decision": "keep"}, candidate, baseline
    )

    assert decision == "keep"
    assert baseline.read_bytes() == candidate_bytes


def test_candidate_mismatch_never_overwrites_baseline(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    baseline = tmp_path / "baseline.json"
    candidate.write_text(
        '{"label_status":"available","result_label_count":1,"score":2}\n',
        encoding="utf-8",
    )
    original = b'{"score":1}\n'
    baseline.write_bytes(original)

    with pytest.raises(ValueError, match="differ from evaluator stdout"):
        validate_and_maybe_promote(
            {"metrics": {"score": 999}, "decision": "keep"}, candidate, baseline
        )

    assert baseline.read_bytes() == original


def test_integrity_named_values_reject_invalid_or_duplicate_names() -> None:
    with pytest.raises(ValueError, match="NAME=VALUE"):
        parse_named(["missing-separator"], option="--file")
    with pytest.raises(ValueError, match="duplicate"):
        parse_named(["input=a", "input=b"], option="--file")
