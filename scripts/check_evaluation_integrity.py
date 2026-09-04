#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path


DEFAULT_KEY_ENV = "EVALUATION_INTEGRITY_KEY"
MANIFEST_SCHEMA_VERSION = 2
SIGNATURE_ALGORITHM = "hmac-sha256"


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


def _validated_key(key: bytes) -> bytes:
    if len(key) < 32:
        raise ValueError("evaluation integrity key must contain at least 32 bytes")
    return key


def load_signing_key(environment_name: str = DEFAULT_KEY_ENV) -> bytes:
    value = os.environ.get(environment_name, "")
    if not value:
        raise ValueError(f"required signing key environment variable is missing: {environment_name}")
    return _validated_key(value.encode("utf-8"))


def _signed_payload(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "snapshot": snapshot,
        "snapshot_sha256": snapshot_sha256(snapshot),
    }


def _payload_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _manifest_signature(payload: dict[str, object], key: bytes) -> str:
    return hmac.new(_validated_key(key), _payload_bytes(payload), hashlib.sha256).hexdigest()


def _verified_manifest_payload(manifest: dict[str, object], key: bytes) -> dict[str, object]:
    if manifest.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        raise ValueError("integrity manifest is unsigned or uses an unsupported signature")
    payload = {
        "schema_version": manifest.get("schema_version"),
        "snapshot": manifest.get("snapshot"),
        "snapshot_sha256": manifest.get("snapshot_sha256"),
    }
    signature = str(manifest.get("signature", ""))
    if not signature or not hmac.compare_digest(signature, _manifest_signature(payload, key)):
        raise ValueError("integrity manifest signature mismatch")
    snapshot = dict(payload.get("snapshot") or {})
    if payload.get("snapshot_sha256") != snapshot_sha256(snapshot):
        raise ValueError("integrity manifest snapshot digest mismatch")
    return payload


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


def create_manifest(
    path: Path,
    snapshot: dict[str, object],
    *,
    key: bytes,
    replace: bool,
    expected_previous_sha256: str = "",
) -> None:
    _validated_key(key)
    if replace:
        if not path.is_file():
            raise ValueError(f"integrity manifest cannot be replaced because it is missing: {path}")
        if not expected_previous_sha256:
            raise ValueError("manifest replacement requires the expected previous SHA-256")
        if not hmac.compare_digest(file_sha256(path), expected_previous_sha256):
            raise ValueError("integrity manifest changed before replacement")
        previous = json.loads(path.read_text(encoding="utf-8"))
        _verified_manifest_payload(previous, key)
    elif path.exists():
        raise ValueError(f"integrity manifest already exists: {path}")
    payload = _signed_payload(snapshot)
    atomic_write_json(
        path,
        {
            **payload,
            "signature_algorithm": SIGNATURE_ALGORITHM,
            "key_id": hashlib.sha256(key).hexdigest()[:16],
            "signature": _manifest_signature(payload, key),
        },
    )


def verify_manifest(path: Path, current: dict[str, object], *, key: bytes) -> None:
    if not path.is_file():
        raise ValueError(f"integrity manifest is missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    payload = _verified_manifest_payload(manifest, key)
    stored = dict(payload.get("snapshot") or {})
    if stored != current:
        raise ValueError("protected evaluation inputs, code, baseline, or parameters changed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or verify immutable evaluation inputs.")
    parser.add_argument("--mode", choices=("create", "verify"), required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--parameter", action="append", default=[])
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--expected-previous-manifest-sha256", default="")
    parser.add_argument("--key-env", default=DEFAULT_KEY_ENV)
    args = parser.parse_args()

    try:
        files = parse_named(args.file, option="--file")
        parameters = parse_named(args.parameter, option="--parameter")
        snapshot = build_snapshot(files, parameters)
        manifest_path = Path(args.manifest)
        key = load_signing_key(args.key_env)
        if args.mode == "create":
            create_manifest(
                manifest_path,
                snapshot,
                key=key,
                replace=args.replace,
                expected_previous_sha256=args.expected_previous_manifest_sha256,
            )
        else:
            if args.replace or args.expected_previous_manifest_sha256:
                raise ValueError("replacement options are only valid in create mode")
            verify_manifest(manifest_path, snapshot, key=key)
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
