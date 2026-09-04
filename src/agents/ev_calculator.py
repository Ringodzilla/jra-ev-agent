from __future__ import annotations

from analysis.candidate_ev import build_candidate_evaluations
from analysis.ev import EVWeights, compute_ev


class EVCalculatorAgent:
    def __init__(self, weights: EVWeights | None = None) -> None:
        self.weights = weights or EVWeights()

    def run(
        self,
        scenario_rows: list[dict[str, object]],
        *,
        combo_odds: list[dict[str, object]] | list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        ev_rows = compute_ev(scenario_rows, weights=self.weights)
        output: dict[str, object] = {"ev_rows": ev_rows}
        if combo_odds is not None:
            output.update(build_candidate_evaluations(ev_rows, combo_odds))
        return output
