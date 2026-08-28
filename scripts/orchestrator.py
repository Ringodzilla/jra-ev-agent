#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jra_scraper.config import ScrapeConfig
from scripts.run_pipeline import load_race_configs
from src.agents import WorkflowSettings
from src.react_workflow import ReactiveRaceWorkflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local multi-agent horse-racing workflow.")
    parser.add_argument("--config-path", default=str(ROOT / "config/races.json"))
    parser.add_argument("--out-dir", default=str(ROOT / "report/stages"))
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--bankroll-per-race", type=int, default=1000)
    parser.add_argument("--min-ev", type=float, default=1.03)
    parser.add_argument("--mode", choices=["balanced", "aggressive"], default="balanced")
    args = parser.parse_args()

    race_configs = load_race_configs(Path(args.config_path))
    config = ScrapeConfig(stages_dir=Path(args.out_dir))
    workflow = ReactiveRaceWorkflow(
        config,
        settings=WorkflowSettings(
            max_repair_attempts=args.max_retries,
            bankroll_per_race=args.bankroll_per_race,
            min_ev=args.min_ev,
            mode=args.mode,
        ),
    )
    outputs = workflow.run(race_configs)
    print(json.dumps(outputs.get("reviewer") or {}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
