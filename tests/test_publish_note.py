import json
import tempfile
import unittest
from pathlib import Path

from scripts.publish_note import (
    load_publish_payload,
    load_note_markdown,
    validate_publish_inputs,
    write_publish_preview,
)


class TestPublishNote(unittest.TestCase):
    def _valid_payload(self) -> dict:
        return {
            "title": "t",
            "tags": ["a"],
            "slug": "s",
            "race_name": "r",
            "race_date": "2026-03-29",
            "body_markdown_path": "report/note.md",
            "artifact_markdown_path": "note_artifact.md",
            "mode_default": "browser:draft",
        }

    def test_missing_payload_file(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                load_publish_payload(Path(td) / "missing.json")

    def test_missing_markdown_file(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                load_note_markdown(Path(td) / "missing.md")

    def test_missing_required_payload_keys(self):
        with tempfile.TemporaryDirectory() as td:
            payload_path = Path(td) / "payload.json"
            payload_path.write_text(json.dumps({"title": "x"}, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_publish_payload(payload_path)

    def test_dry_run_preview_generation(self):
        with tempfile.TemporaryDirectory() as td:
            payload_path = Path(td) / "payload.json"
            note_path = Path(td) / "note.md"
            artifact_path = Path(td) / "note_artifact.md"
            preview_path = Path(td) / "publish_preview.txt"

            payload_path.write_text(json.dumps(self._valid_payload(), ensure_ascii=False), encoding="utf-8")
            note_path.write_text("# test note", encoding="utf-8")
            artifact_path.write_text("# test note", encoding="utf-8")

            payload, note = validate_publish_inputs(payload_path, note_path)
            write_publish_preview(preview_path, payload, note, intended_mode="dry-run")

            self.assertTrue(preview_path.exists())
            body = preview_path.read_text(encoding="utf-8")
            self.assertIn("intended_mode: dry-run", body)
            self.assertIn("race_date: 2026-03-29", body)

    def test_publish_validation_rejects_unsynced_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            payload_path = Path(td) / "payload.json"
            note_path = Path(td) / "note.md"
            artifact_path = Path(td) / "note_artifact.md"

            payload_path.write_text(json.dumps(self._valid_payload(), ensure_ascii=False), encoding="utf-8")
            note_path.write_text("# test note", encoding="utf-8")
            artifact_path.write_text("# stale artifact", encoding="utf-8")

            with self.assertRaises(ValueError):
                validate_publish_inputs(payload_path, note_path)


if __name__ == "__main__":
    unittest.main()
