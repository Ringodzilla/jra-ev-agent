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
                            _attach_environment_values(row, snapshot)
        finally:
            pipeline.close()

        quality_report = _read_json(self.config.quality_report_path)
        quality_report["environment_observations"] = [
            {
                "race_id": snapshot.get("race_id", ""),
                "status": snapshot.get("status", ""),
                "collection_complete": bool(snapshot.get("collection_complete")),
                "eligible_for_learning": bool(snapshot.get("eligible_for_learning")),
                "missing_fields": list(snapshot.get("missing_fields") or []),
                "errors": list(snapshot.get("errors") or []),
            }
            for snapshot in environment_snapshots
        ]
        quality_report["environment_collection_complete"] = bool(environment_snapshots) and all(
            snapshot.get("collection_complete") is True for snapshot in environment_snapshots
        )

        return {
            "environment_snapshots": environment_snapshots,
            "collector_key": self.collector_key,
            "aggressive_repair": effective_aggressive_repair,
            "race_configs": race_configs,
            "rows": rows,
            "entries": _read_csv(self.config.entries_csv),
            "odds_snapshots": _read_csv(self.config.odds_snapshots_csv),
            "combo_odds": _read_csv(self.config.combo_odds_csv),
            "quality_report": quality_report,
        }


class DomesticDataCollectorAgent(DataCollectorAgent):
    collector_key = "domestic"
    default_aggressive_repair = False


class OverseasDataCollectorAgent(DataCollectorAgent):
    collector_key = "overseas"
    default_aggressive_repair = True


def _attach_environment_values(row: dict, snapshot: dict) -> None:
    for key in (
        'environment_schema_version', 'captured_at', 'cushion_measured_at',
        'moisture_measured_at', 'cushion', 'turf_moisture_goal',
        'turf_moisture_corner4', 'dirt_moisture_goal', 'dirt_moisture_corner4',
        'turf_moisture_goal_condition', 'turf_moisture_corner4_condition',
        'dirt_moisture_goal_condition', 'dirt_moisture_corner4_condition',
        'moisture_note', 'collection_complete', 'eligible_for_learning',
    ):
        row[f'target_environment_{key}'] = snapshot.get(key, '')


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
