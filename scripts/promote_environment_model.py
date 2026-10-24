#!/usr/bin/env python3
"""Explicitly promote a held-out-approved environment model for inference."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-report", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/models/environment_model.json",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Required acknowledgement that the reviewed candidate may affect production probabilities.",
    )
    args = parser.parse_args()
    if not args.approve:
        raise SystemExit("refusing promotion without --approve")

    report = json.loads(args.training_report.read_text(encoding="utf-8"))
    if report.get("status") != "ELIGIBLE_FOR_REVIEW":
        raise SystemExit("only ELIGIBLE_FOR_REVIEW candidates can be promoted")
    model = dict(report.get("selected_model") or {})
    if model.get("schema_version") != 1:
        raise SystemExit("unsupported environment model schema")
    model.setdefault("supported_surfaces", ["芝"])
    report_sha256 = hashlib.sha256(args.training_report.read_bytes()).hexdigest()
    model_id = f"environment-{report.get('dataset_sha256', report_sha256)[:12]}-{report_sha256[:12]}"
    artifact = {
        "schema_version": 1,
        "status": "PROMOTED",
        "production_model_changed": True,
        "model_id": model_id,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "training_report_path": str(args.training_report),
        "training_report_sha256": report_sha256,
        "dataset_sha256": report.get("dataset_sha256", ""),
        "model_code_sha256": report.get("model_code_sha256", ""),
        "model": model,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PROMOTED", "model_id": model_id, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
