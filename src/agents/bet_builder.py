from __future__ import annotations

from strategy.betting import generate_tickets
from strategy.win5 import generate_win5_plan, is_win5_mode

from src.agents.race_utils import _race_order_from_configs
from src.agents.settings import WorkflowSettings


class BetBuilderAgent:
    def __init__(self, settings: WorkflowSettings) -> None:
        self.settings = settings

    def run(
        self,
        ev_rows: list[dict[str, object]],
        *,
        combo_odds: list[dict[str, object]] | list[dict[str, str]] | None = None,
        odds_history: list[dict[str, object]] | list[dict[str, str]] | None = None,
        candidate_evaluations: list[dict[str, object]] | None = None,
        candidate_validation: dict[str, object] | None = None,
        seconds_to_post: float | None = None,
        race_configs: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        if is_win5_mode(self.settings.mode):
            return generate_win5_plan(
                ev_rows,
                mode=self.settings.mode,
                max_points=self.settings.win5_max_points,
                stake_yen_per_point=self.settings.win5_stake_yen_per_point,
                race_order=_race_order_from_configs(race_configs or []),
            )

        return generate_tickets(
            ev_rows,
            odds_rows=list(combo_odds or []),
            odds_history=list(odds_history if odds_history is not None else combo_odds or []),
            candidate_evaluations=candidate_evaluations,
            candidate_validation=candidate_validation,
            seconds_to_post=seconds_to_post,
            mode=self.settings.mode,
            bankroll_per_race=self.settings.bankroll_per_race,
            min_ev=self.settings.min_ev,
            min_place_ev=self.settings.min_place_ev,
            min_wide_ev=self.settings.min_wide_ev,
            min_wakuren_ev=self.settings.min_wakuren_ev,
            min_umaren_ev=self.settings.min_umaren_ev,
            min_umatan_ev=self.settings.min_umatan_ev,
            min_sanrenpuku_ev=self.settings.min_sanrenpuku_ev,
            min_sanrentan_ev=self.settings.min_sanrentan_ev,
            max_tickets_per_race=self.settings.max_tickets_per_race,
            max_wide_tickets_per_race=self.settings.max_wide_tickets_per_race,
            max_exotic_tickets_per_race=self.settings.max_exotic_tickets_per_race,
            min_portfolio_ev=self.settings.min_portfolio_ev,
            min_coverage_ev=self.settings.min_coverage_ev,
            prefer_wide=self.settings.prefer_wide,
            max_horse_stake_dependency_ratio=self.settings.max_horse_stake_dependency_ratio,
        )
