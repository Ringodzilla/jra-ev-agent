from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkflowSettings:
    max_repair_attempts: int = 1
    bankroll_per_race: int = 1000
    min_ev: float = 1.03
    min_place_ev: float = 1.01
    min_wide_ev: float = 1.01
    min_wakuren_ev: float = 1.03
    min_umaren_ev: float = 1.04
    min_umatan_ev: float = 1.07
    min_sanrenpuku_ev: float = 1.06
    min_sanrentan_ev: float = 1.12
    max_tickets_per_race: int = 5
    max_wide_tickets_per_race: int = 2
    max_exotic_tickets_per_race: int = 4
    min_portfolio_ev: float = 1.0
    min_coverage_ev: float = 0.75
    mode: str = "balanced"
    win5_max_points: int | None = None
    win5_stake_yen_per_point: int = 100
    prefer_wide: bool = True
    max_ev_delta_abs: float = 0.20
    max_ev_delta_ratio: float = 0.18
    max_odds_gap_ratio: float = 0.25
    min_top3_ticket_coverage: int = 2
    max_horse_ticket_dependency_ratio: float = 0.50
    max_horse_stake_dependency_ratio: float = 0.60
