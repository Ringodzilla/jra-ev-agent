from __future__ import annotations

from analysis.ev import EVWeights, compute_ev


class EVCalculatorAgent:
    def __init__(self, weights: EVWeights | None = None) -> None:
        self.weights = weights or EVWeights()

    def run(self, scenario_rows: list[dict[str, object]]) -> dict[str, object]:
        return {"ev_rows": compute_ev(scenario_rows, weights=self.weights)}
