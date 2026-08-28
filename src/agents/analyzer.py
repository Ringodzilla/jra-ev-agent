from __future__ import annotations

from analysis.ev import build_feature_rows


class AnalyzerAgent:
    def run(
        self,
        rows: list[dict[str, str]],
        *,
        odds_snapshots: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        return {"feature_rows": build_feature_rows(rows, odds_snapshots=odds_snapshots)}
