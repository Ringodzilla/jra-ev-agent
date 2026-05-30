#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REQUIRED_PAYLOAD_KEYS = {
    "title",
    "tags",
    "slug",
    "race_name",
    "race_date",
    "body_markdown_path",
    "artifact_markdown_path",
    "mode_default",
}


def load_publish_payload(payload_path: Path) -> dict:
    if not payload_path.exists():
        raise FileNotFoundError(f"payload file not found: {payload_path}")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_PAYLOAD_KEYS - set(payload.keys()))
    if missing:
        raise ValueError(f"payload missing required keys: {missing}")
    return payload


def load_note_markdown(note_path: Path) -> str:
    if not note_path.exists():
        raise FileNotFoundError(f"note markdown file not found: {note_path}")
    return note_path.read_text(encoding="utf-8")


def validate_publish_inputs(payload_path: Path, note_path: Path) -> tuple[dict, str]:
    logging.info("validation start payload=%s note=%s", payload_path, note_path)
    payload = load_publish_payload(payload_path)
    note = load_note_markdown(note_path)
    artifact_path = _resolve_payload_path(payload_path, str(payload["artifact_markdown_path"]))
    artifact = load_note_markdown(artifact_path)
    if not artifact.strip():
        raise ValueError(f"artifact markdown is empty: {artifact_path}")
    if artifact != note:
        raise ValueError(f"artifact markdown is not synced with note markdown: {artifact_path}")
    logging.info("validation success")
    return payload, note


def _resolve_payload_path(payload_path: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    sibling = payload_path.parent / candidate
    if sibling.exists():
        return sibling
    return payload_path.parent.parent / candidate


def write_publish_preview(preview_path: Path, payload: dict, note: str, intended_mode: str) -> None:
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(
        "\n".join(
            [
                f"title: {payload.get('title')}",
                f"tags: {', '.join(payload.get('tags', []))}",
                f"slug: {payload.get('slug')}",
                f"race_name: {payload.get('race_name')}",
                f"race_date: {payload.get('race_date')}",
                f"intended_mode: {intended_mode}",
                "",
                note,
            ]
        ),
        encoding="utf-8",
    )
    logging.info("preview generated: %s", preview_path)


def run_browser_mode(payload: dict, note: str, mode: str, report_dir: Path) -> None:
    logging.info("browser mode start mode=%s", mode)
    plan = {
        "mode": "browser",
        "action": mode,
        "title": payload["title"],
        "tags": payload["tags"],
        "slug": payload["slug"],
        "race_name": payload["race_name"],
        "race_date": payload["race_date"],
        "body_preview_length": len(note),
        "steps": [
            "Open note editor in authenticated browser session",
            "Inject title/body/tags",
            "If draft: stop before final publish",
            "If publish: require explicit final confirmation",
        ],
    }
    plan_path = report_dir / "browser_publish_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    raise RuntimeError(
        "Browser automation runtime is not configured. "
        f"Review and execute plan manually: {plan_path}. "
        "For automation, install and configure a supported browser driver/session."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish note utility")
    parser.add_argument("--dry-run", action="store_true", help="Generate preview only")
    parser.add_argument("--mode", choices=["browser"], help="Publishing mode")
    parser.add_argument("--draft", action="store_true", help="Browser mode: draft only")
    parser.add_argument("--publish", action="store_true", help="Browser mode: publish")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()

    payload_path = ROOT / "report/publish_payload.json"
    note_path = ROOT / "report/note.md"
    preview_path = ROOT / "report/publish_preview.txt"

    payload, note = validate_publish_inputs(payload_path, note_path)

    if args.dry_run:
        write_publish_preview(preview_path, payload, note, intended_mode="dry-run")
        logging.info("dry-run completed successfully")
        return

    if args.mode == "browser":
        if args.publish and args.draft:
            raise ValueError("choose only one of --draft or --publish")
        action_mode = "publish" if args.publish else "draft"
        write_publish_preview(preview_path, payload, note, intended_mode=f"browser:{action_mode}")
        run_browser_mode(payload, note, action_mode, ROOT / "report")
        return

    raise ValueError("choose --dry-run or --mode browser")


if __name__ == "__main__":
    main()
