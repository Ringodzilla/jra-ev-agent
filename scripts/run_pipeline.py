#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.ev import save_ev
from jra_scraper.config import ScrapeConfig
from report.note import generate_note_markdown, write_note_artifacts
from src.react_workflow import ReactiveRaceWorkflow, WorkflowSettings


REQUIRED_CONFIG_KEYS = {
    "race_name",
    "race_date",
    "track",
    "race_number",
    "source_url",
    "output_slug",
    "note_tags",
}


def load_race_configs(path: Path) -> list[dict]:
    configs = json.loads(path.read_text(encoding="utf-8"))
    for idx, cfg in enumerate(configs, start=1):
        missing = sorted(REQUIRED_CONFIG_KEYS - set(cfg.keys()))
        if missing:
            raise ValueError(f"config index={idx} missing keys: {missing}")
    return configs


def run_analysis_phase(
    race_configs: list[dict],
    *,
    force_rebuild: bool = False,
    race_limit: int | None = None,
    horse_limit: int | None = None,
    reprocess_raw: bool = False,
    max_repairs: int = 1,
    bankroll_per_race: int = 1000,
    min_ev: float = 1.03,
    mode: str = "balanced",
) -> dict:
    logging.info("analysis phase started")

    config = ScrapeConfig()
    workflow = ReactiveRaceWorkflow(
        config,
        settings=WorkflowSettings(
            max_repair_attempts=max_repairs,
            bankroll_per_race=bankroll_per_race,
            min_ev=min_ev,
            mode=mode,
        ),
    )
    outputs = workflow.run(
        race_configs,
        force_rebuild=force_rebuild,
        race_limit=race_limit,
        horse_limit=horse_limit,
        reprocess_raw=reprocess_raw,
    )

    ev_rows = list(dict(outputs.get("ev_calculator") or {}).get("ev_rows") or [])
    review = dict(outputs.get("reviewer") or {})
    quality_report = dict(dict(outputs.get("data_collector") or {}).get("quality_report") or {})
    tickets = dict(outputs.get("bet_builder") or {})
    article = dict(outputs.get("article_writer") or {})

    ev_path = ROOT / "data/processed/race_ev.csv"
    note_path = ROOT / "report/note.md"
    payload_path = ROOT / "report/publish_payload.json"
    run_path = ROOT / "report/pipeline_run.json"

    save_ev(ev_rows, ev_path)
    primary_race_name = race_configs[0]["race_name"] if race_configs else "JRAレース"
    note = str(
        article.get("markdown")
        or generate_note_markdown(
            primary_race_name,
            ev_rows,
            tickets,
            review=review,
            quality_report=quality_report,
            race_config=race_configs[0] if race_configs else {},
        )
    )
    artifact_result = write_note_artifacts(note_path, note)

    payload = {
        "title": article.get("title") or (race_configs[0].get("note_title") if race_configs else "jra-ev-agent analysis"),
        "tags": race_configs[0]["note_tags"] if race_configs else ["競馬", "EV", "JRA"],
        "slug": race_configs[0]["output_slug"] if race_configs else "jra-ev-analysis",
        "race_name": primary_race_name,
        "race_date": race_configs[0]["race_date"] if race_configs else "",
        "body_markdown_path": artifact_result.body_markdown_path,
        "artifact_markdown_path": artifact_result.artifact_markdown_path,
        "artifact_exists": artifact_result.artifact_exists,
        "artifact_size_bytes": artifact_result.artifact_size_bytes,
        "artifact_synced": artifact_result.artifact_synced,
        "artifact": artifact_result.to_dict(),
        "mode_default": "browser:draft",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "review_status": review.get("status", "UNKNOWN"),
        "article_status": article.get("status", "unknown"),
        "quality_report_path": str(config.quality_report_path),
        "ev_csv_path": str(ev_path),
    }
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    run_path.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("analysis phase completed")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fully orchestrated JRA EV workflow")
    parser.add_argument("--config-path", default=str(ROOT / "config/races.json"), help="Race config json path")
    parser.add_argument("--force-rebuild", action="store_true", help="Ignore processed state and rebuild races")
    parser.add_argument("--reprocess-raw", action="store_true", help="Parse only cached raw HTML and avoid network fetches")
    parser.add_argument("--race-limit", type=int, default=None, help="Max races to process (default: no limit)")
    parser.add_argument("--horse-limit", type=int, default=None, help="Max horses per race (default: no limit)")
    parser.add_argument("--max-repairs", type=int, default=1, help="How many self-repair retries to allow")
    parser.add_argument("--bankroll-per-race", type=int, default=1000, help="Budget cap per race in yen")
    parser.add_argument("--min-ev", type=float, default=1.03, help="Minimum EV required for ticket generation")
    parser.add_argument("--mode", choices=["balanced", "aggressive"], default="balanced", help="Ticketing mode")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    race_configs = load_race_configs(Path(args.config_path))
    payload = run_analysis_phase(
        race_configs,
        force_rebuild=args.force_rebuild,
        race_limit=args.race_limit,
        horse_limit=args.horse_limit,
        reprocess_raw=args.reprocess_raw,
        max_repairs=args.max_repairs,
        bankroll_per_race=args.bankroll_per_race,
        min_ev=args.min_ev,
        mode=args.mode,
    )
    logging.info("outputs ready: %s", payload)


if __name__ == "__main__":
    main()
