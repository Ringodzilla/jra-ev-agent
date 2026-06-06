#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_pipeline import load_race_configs, run_analysis_phase


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WIN5 formation generation")
    parser.add_argument("--config-path", default=str(ROOT / "config/win5_races.json"), help="Five-race config json path")
    parser.add_argument(
        "--mode",
        choices=["win5_under_10", "win5_compact", "win5_balanced", "win5_value"],
        default="win5_compact",
        help="WIN5 formation mode",
    )
    parser.add_argument("--max-points", type=int, default=None, help="Maximum WIN5 formation points")
    parser.add_argument("--stake-yen-per-point", type=int, default=100, help="Stake per WIN5 point")
    parser.add_argument("--force-rebuild", action="store_true", help="Ignore processed state and rebuild races")
    parser.add_argument("--reprocess-raw", action="store_true", help="Parse only cached raw HTML and avoid network fetches")
    parser.add_argument("--max-repairs", type=int, default=1, help="How many self-repair retries to allow")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    race_configs = load_race_configs(Path(args.config_path))
    run_analysis_phase(
        race_configs,
        force_rebuild=args.force_rebuild,
        reprocess_raw=args.reprocess_raw,
        max_repairs=args.max_repairs,
        mode=args.mode,
        win5_max_points=args.max_points,
        win5_stake_yen_per_point=args.stake_yen_per_point,
    )


if __name__ == "__main__":
    main()
