from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.build_fixed_evaluation_dataset import build_fixed_dataset


def _write_artifact(root: Path, race_id: str, history_date: str = "2026-08-01") -> None:
    path = root / race_id / "pipeline_run.json"
    path.parent.mkdir(parents=True)
    payload = {
        "data_collector": {
            "rows": [
                {
                    "row_id": f"{race_id}-2",
                    "race_id": race_id,
                    "horse_id": "h2",
                    "horse_name": "Second",
                    "horse_number": "2",
                    "run_index": "1",
                    "target_race_date": "2026-08-02",
                    "date": history_date,
                },
                {
                    "row_id": f"{race_id}-1",
                    "race_id": race_id,
                    "horse_id": "h1",
                    "horse_name": "First",
                    "horse_number": "1",
                    "run_index": "1",
                    "target_race_date": "2026年8月2日",
                    "date": history_date,
                },
            ]
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_results(path: Path) -> None:
    path.write_text(
        "race_id,式別,組番,馬番,払戻金\n"
        "race_a,単勝,,1,200\n"
        "race_missing,単勝,,3,500\n"
        "WIN5,WIN5,1-2-3-4-5,,10000\n",
        encoding="utf-8",
    )


def test_build_fixed_dataset_intersects_labels_and_writes_manifest(tmp_path: Path) -> None:
    artifacts = tmp_path / "races"
    results = tmp_path / "results.csv"
    output = tmp_path / "fixed.csv"
    manifest_path = tmp_path / "fixed.manifest.json"
    fixed_results = tmp_path / "fixed_results.csv"
    _write_artifact(artifacts, "race_a")
    _write_artifact(artifacts, "race_unlabeled")
    _write_results(results)

    manifest = build_fixed_dataset(
        artifacts_root=artifacts,
        results_path=results,
        output_path=output,
        manifest_path=manifest_path,
        output_results_path=fixed_results,
    )

    with output.open(encoding="utf-8", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert [row["horse_number"] for row in rows] == ["1", "2"]
    assert manifest["selected_races"] == ["race_a"]
    assert manifest["labeled_races_missing_artifacts"] == ["race_missing"]
    assert manifest["race_count"] == 1
    assert manifest["row_count"] == 2
    assert manifest["output_results"]["row_count"] == 1
    assert list(csv.DictReader(fixed_results.open(encoding="utf-8")))[0]["race_id"] == "race_a"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["output"]["sha256"]


@pytest.mark.parametrize(
    ("history_date", "extra", "expected"),
    [
        ("2026-08-02", {}, "is not before target date"),
        ("2026-08-01", {"result_position": 1}, "leakage-prone fields"),
    ],
)
def test_build_fixed_dataset_rejects_leakage(
    tmp_path: Path,
    history_date: str,
    extra: dict[str, object],
    expected: str,
) -> None:
    artifacts = tmp_path / "races"
    results = tmp_path / "results.csv"
    output = tmp_path / "fixed.csv"
    manifest_path = tmp_path / "fixed.manifest.json"
    _write_artifact(artifacts, "race_a", history_date=history_date)
    path = artifacts / "race_a" / "pipeline_run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["data_collector"]["rows"][0].update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _write_results(results)

    with pytest.raises(ValueError, match=expected):
        build_fixed_dataset(
            artifacts_root=artifacts,
            results_path=results,
            output_path=output,
            manifest_path=manifest_path,
        )
