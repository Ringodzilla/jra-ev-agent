from __future__ import annotations

import csv
import json
from pathlib import Path

from jra_scraper.config import ScrapeConfig
from jra_scraper.pipeline import JRAPipeline
from jra_scraper.environment import capture_environment


class DataCollectorAgent:
    collector_key: str = "domestic"
    default_aggressive_repair: bool = False

    def __init__(self, config: ScrapeConfig) -> None:
        self.config = config

    def run(
        self,
        race_configs: list[dict[str, object]],
        *,
        force_rebuild: bool = False,
        race_limit: int | None = None,
        horse_limit: int | None = None,
        aggressive_repair: bool = False,
        reprocess_raw: bool = False,
    ) -> dict[str, object]:
        effective_aggressive_repair = aggressive_repair or self.default_aggressive_repair
        pipeline = JRAPipeline(self.config)
        try:
            rows = pipeline.run(
                race_specs=race_configs,
                force_rebuild=force_rebuild,
                race_limit=race_limit,
                horse_limit=horse_limit,
                aggressive_repair=effective_aggressive_repair,
                reprocess_raw=reprocess_raw,
            )
            environment_snapshots = [capture_environment(
                pipeline.scraper, cfg, rows, self.config.stages_dir.parent / 'environment_observations',
                cache_only=reprocess_raw,
            ) for cfg in race_configs]
            for snapshot in environment_snapshots:
                if snapshot.get('weather') and snapshot.get('track_condition'):
                    for row in rows:
                        if row.get('race_id') == snapshot['race_id']:
                            row.update(target_weather=snapshot['weather'],
                                       target_track_condition=snapshot['track_condition'],
                                       target_conditions_captured_at=snapshot['weather_captured_at'])
        finally:
            pipeline.close()

        return {
            "environment_snapshots": environment_snapshots,
            "collector_key": self.collector_key,
            "aggressive_repair": effective_aggressive_repair,
            "race_configs": race_configs,
            "rows": rows,
            "entries": _read_csv(self.config.entries_csv),
            "odds_snapshots": _read_csv(self.config.odds_snapshots_csv),
            "combo_odds": _read_csv(self.config.combo_odds_csv),
            "quality_report": _read_json(self.config.quality_report_path),
        }


class DomesticDataCollectorAgent(DataCollectorAgent):
    collector_key = "domestic"
    default_aggressive_repair = False


class OverseasDataCollectorAgent(DataCollectorAgent):
    collector_key = "overseas"
    default_aggressive_repair = True


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
