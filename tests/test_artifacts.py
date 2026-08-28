import hashlib
import json
from pathlib import Path

from src.artifacts import atomic_write_json, file_sha256


def test_atomic_write_json_creates_parent_and_preserves_unicode(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "stage.json"
    payload = {"status": "OK", "horse": "テストホース"}

    atomic_write_json(output, payload)

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert not list(output.parent.glob(".*.tmp"))


def test_file_sha256_matches_hashlib(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b'{"status":"OK"}')

    assert file_sha256(artifact) == hashlib.sha256(artifact.read_bytes()).hexdigest()
