from __future__ import annotations

from analysis.ev import simulate_race_scenarios


class SimulatorAgent:
    def run(self, feature_rows: list[dict[str, object]]) -> dict[str, object]:
        return {"scenario_rows": simulate_race_scenarios(feature_rows)}
