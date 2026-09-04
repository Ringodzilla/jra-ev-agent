#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_named(values: list[str], *, option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        name, separator, raw = value.partition("=")
        if not separator or not name.strip() or not raw.strip():
            raise ValueError(f"{option} requires NAME=VALUE, got {value!r}")
        if name in parsed:
            raise ValueError(f"duplicate {option} name: {name}")
        parsed[name] = raw
    return parsed


def build_snapshot(files: dict[str, str], parameters: dict[str, str]) -> dict[str, object]:
    records: dict[str, object] = {}
    for name, raw_path in sorted(files.items()):
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"protected file is missing: {name}={path}")
        records[name] = {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
    return {
        "schema_version": 1,
        "files": records,
        "parameters": dict(sorted(parameters.items())),
    }


def snapshot_sha256(snapshot: dict[str, object]) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2, sort_keys=True)
            file_obj.write("\n")
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def create_manifest(path: Path, snapshot: dict[str, object], *, replace: bool) -> None:
    if path.exists() and not replace:
        raise ValueError(f"integrity manifest already exists: {path}")
    atomic_write_json(
        path,
        {
            "snapshot": snapshot,
            "snapshot_sha256": snapshot_sha256(snapshot),
        },
    )


def verify_manifest(path: Path, current: dict[str, object]) -> None:
    if not path.is_file():
        raise ValueError(f"integrity manifest is missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    stored = dict(manifest.get("snapshot") or {})
    stored_digest = str(manifest.get("snapshot_sha256", ""))
    if not stored_digest or stored_digest != snapshot_sha256(stored):
        raise ValueError("integrity manifest digest mismatch")
    if stored != current:
        raise ValueError("protected evaluation inputs, code, baseline, or parameters changed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or verify immutable evaluation inputs.")
    parser.add_argument("--mode", choices=("create", "verify"), required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--parameter", action="append", default=[])
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    try:
        files = parse_named(args.file, option="--file")
        parameters = parse_named(args.parameter, option="--parameter")
        snapshot = build_snapshot(files, parameters)
        manifest_path = Path(args.manifest)
        if args.mode == "create":
            create_manifest(manifest_path, snapshot, replace=args.replace)
        else:
            verify_manifest(manifest_path, snapshot)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "NG", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)

    print(
        json.dumps(
            {
                "status": "OK",
                "mode": args.mode,
                "manifest": str(Path(args.manifest)),
                "snapshot_sha256": snapshot_sha256(snapshot),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
