from __future__ import annotations

from pathlib import Path

from analysis.ev import build_feature_rows
from src.environment_prediction import apply_environment_features


class AnalyzerAgent:
    def __init__(self, *, environment_model_path: Path | None = None) -> None:
        self.environment_model_path = environment_model_path

    def run(
        self,
        rows: list[dict[str, str]],
        *,
        odds_snapshots: list[dict[str, str]] | None = None,
        environment_snapshots: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        feature_rows = build_feature_rows(rows, odds_snapshots=odds_snapshots)
        feature_rows, environment_application = apply_environment_features(
            feature_rows,
            environment_snapshots,
            production_model_path=self.environment_model_path,
        )
        return {
            "feature_rows": feature_rows,
            "environment_application": environment_application,
        }
