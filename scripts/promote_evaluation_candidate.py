#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file_obj:
            file_obj.write(payload)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def validate_and_maybe_promote(
    reported: dict[str, object],
    candidate_path: Path,
    baseline_path: Path,
) -> str:
    """Validate one byte snapshot and promote those exact bytes when approved."""
    candidate_bytes = candidate_path.read_bytes()
    candidate = json.loads(candidate_bytes)
    if not isinstance(reported, dict) or not isinstance(candidate, dict):
        raise ValueError("evaluator output and candidate must be JSON objects")
    if reported.get("metrics") != candidate:
        raise ValueError("candidate metrics differ from evaluator stdout")
    if candidate.get("label_status") != "available":
        raise ValueError("candidate result labels are unavailable")
    if int(candidate.get("result_label_count", 0)) <= 0:
        raise ValueError("candidate has no result labels")
    decision = str(reported.get("decision", ""))
    if decision not in {"keep", "revert"}:
        raise ValueError("evaluator returned an invalid decision")
    if decision == "keep":
        atomic_write_bytes(baseline_path, candidate_bytes)
    return decision


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate evaluator output and atomically promote the identical candidate bytes."
    )
    parser.add_argument("--reported-json", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()

    try:
        reported = json.loads(args.reported_json)
        decision = validate_and_maybe_promote(
            reported,
            Path(args.candidate),
            Path(args.baseline),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "NG", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
    print(decision)


if __name__ == "__main__":
    main()
