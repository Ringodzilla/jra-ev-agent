from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_storage_audit_applies_scope_specific_retention(tmp_path: Path) -> None:
    fixtures = (
        ("report/races/20260701_tokyo_11", "race"),
        ("report/races/direct_manual", "manual"),
        ("data/collected/20260701_tokyo_11", "collected"),
        ("report/final_predictions/20250101_tokyo_11", "prediction"),
    )
    for relative_path, content in fixtures:
        artifact = tmp_path / relative_path
        artifact.mkdir(parents=True)
        (artifact / "artifact.txt").write_text(content, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "audit_artifact_storage.py"),
            "--root",
            str(tmp_path),
            "--as-of",
            "2026-08-29",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "\r" not in result.stdout
    rows = {
        (row["scope"], row["artifact"]): row
        for row in csv.DictReader(result.stdout.splitlines(), delimiter="\t")
    }
    assert rows[("report/races", "20260701_tokyo_11")]["action"] == "archive-candidate"
    assert rows[("report/races", "direct_manual")]["action"] == "manual-review"
    assert rows[("data/collected", "20260701_tokyo_11")]["action"] == "keep-active"
    assert (
        rows[("report/final_predictions", "20250101_tokyo_11")]["action"]
        == "archive-candidate"
    )


def test_storage_audit_rejects_non_positive_override(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "audit_artifact_storage.py"),
            "--root",
            str(tmp_path),
            "--retention-days",
            "0",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must be greater than zero" in result.stderr
