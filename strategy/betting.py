from __future__ import annotations

from collections import defaultdict
from itertools import combinations, permutations
import math

from analysis.candidate_ev import PROBABILITY_SUM_TOLERANCE, canonical_combination
from strategy.live_odds import (
    build_live_odds_lookup as _build_live_odds_lookup,
    live_odds_value as _live_odds_value,
    lookup_live_odds as _lookup_live_odds,
)
from strategy.odds_scenarios import build_odds_scenarios
from strategy.portfolio import (
    is_formation_ticket as _is_formation_ticket,
    is_wide_box_ticket as _is_wide_box_ticket,
    portfolio_ev as _portfolio_ev,
    portfolio_expected_return as _portfolio_expected_return,
    portfolio_no_gami as _portfolio_no_gami,
    portfolio_robust_ev as _portfolio_robust_ev,
    portfolio_robust_expected_return as _portfolio_robust_expected_return,
    portfolio_summary as _portfolio_summary,
    portfolio_total_points as _portfolio_total_points,
    portfolio_total_stake as _portfolio_total_stake,
    ticket_max_return_if_hit as _ticket_max_return_if_hit,
    ticket_return_if_hit as _ticket_return_if_hit,
    ticket_stake_unit as _ticket_stake_unit,
    with_adjusted_stake as _with_adjusted_stake,
)
from strategy.betting_common import (
    MIN_WIN_EV,
    MIN_ACTIONABLE_WIN_EV,
    BET_TYPE_MARKET_SHRINK,
    _to_float,
    _clamp,
    _fmt,
    _combo_name,
    _ticket_horse_numbers,
    _dedupe_rows_by_horse_number,
    _axis_values,
    _formation_axis_rows,
    _pair_strings,
    _unique_horse_names,
    _kelly_stake,
    _ticket_market_shrink,
    _ticket_selection_key,
    _selection_type_order,
    _is_coverage_ticket,
    _is_model_pair_coverage_ticket,
    _coverage_priority,
    _prioritize_exotic_types,
    _probability_band_from_row,
    _frame_horse_summaries,
    _resolve_frame_number,
    _frame_sort_key,
    _is_top_two_model_pair,
    _with_long_reason,
    _horse_summary,
)
from strategy.betting_estimates import (
    _normalize_to_target,
    _scale_to_target,
    _estimate_pair_hit_prob,
    _estimate_market_pair_prob,
    _wide_pace_adjustment,
    _estimate_market_pair_odds,
    _estimate_place_odds,
    _estimate_predicted_pair_odds,
    _estimate_predicted_combo_odds,
    _combo_hit_prob,
    _ordered_finish_prob,
    _estimate_exotic_odds,
    _frame_combo_hit_prob,
    _frame_quality_adjustment,
    _single_frame_quality,
)
from strategy.betting_tickets import (
    _canonical_ticket_from_evaluation,
    _build_top5_wide_box_candidate,
    _wide_box_outcome_audit,
    _build_win_ticket,
    _build_place_ticket,
    _build_wide_ticket,
    _build_wakuren_ticket,
    _build_exotic_ticket,
    _formation_points,
)
from strategy.betting_selection import (
    _probability_lineage_failure_plan,
    _index_odds_history,
    _ticket_decision_ev,
    _ticket_decision_probability,
    _ticket_decision_odds,
    _rank_ticket_pool,
    _ticket_type_rank,
    _limit_eligible_selection_pool,
    _select_optimized_tickets,
    _append_ticket_if_new,
    _would_exceed_horse_dependency,
    _dedupe_ticket_combos,
    _calibrate_ticket_probabilities,
    _portfolio_horse_numbers,
    _top_win_probability_horse_number,
    _top_win_probability_horse_numbers,
    _max_horse_stake_dependency_ratio,
    _candidate_eligibility_checks,
    _annotate_candidate_selection,
    _is_top3_rank_coverage_ticket,
    _can_add_coverage_ticket,
    _prune_gami_tickets,
    _annotate_portfolio_tickets,
    _candidate_counts_by_type,
    _labels_for_type,
    _ticket_combo_label,
    _reconcile_ticket_classifications,
    _ticket_horse_numbers_for_classification,
    _ticket_max_portfolio_stake,
    _stake_allocation_score,
    _rebalance_race_stakes,
    _has_win_standout,
)
from strategy.betting_candidates import (
    _promote_top3_rank_coverage_candidates,
    _build_place_candidates,
    _build_wide_candidates,
    _build_wakuren_candidates,
    _build_exotic_candidates,
    _build_sanrentan_formation_candidates,
    _formation_axis_label,
    _build_coverage_candidates,
    _build_model_consistency_candidates,
    _build_win_ev_translation_candidates,
    _build_marked_top5_coverage_candidates,
    _build_race_longshots,
    _front_value_candidates,
    _model_score_longshots,
    _has_longshot_structural_support,
    _enrich_rows_for_multi_bet,
    _is_win_ev_translation_row,
    _win_ev_translation_score,
    _live_or_current_win_odds,
    _win_candidate_ev,
)


__all__ = [
    "defaultdict",
    "combinations",
    "permutations",
    "math",
    "PROBABILITY_SUM_TOLERANCE",
    "canonical_combination",
    "_build_live_odds_lookup",
    "_live_odds_value",
    "_lookup_live_odds",
    "build_odds_scenarios",
    "_is_formation_ticket",
    "_is_wide_box_ticket",
    "_portfolio_ev",
    "_portfolio_expected_return",
    "_portfolio_no_gami",
    "_portfolio_robust_ev",
    "_portfolio_robust_expected_return",
    "_portfolio_summary",
    "_portfolio_total_points",
    "_portfolio_total_stake",
    "_ticket_max_return_if_hit",
    "_ticket_return_if_hit",
    "_ticket_stake_unit",
    "_with_adjusted_stake",
    "MIN_WIN_EV",
    "MIN_ACTIONABLE_WIN_EV",
    "BET_TYPE_MARKET_SHRINK",
    "_to_float",
    "_clamp",
    "_fmt",
    "_combo_name",
    "_ticket_horse_numbers",
    "_dedupe_rows_by_horse_number",
    "_axis_values",
    "_formation_axis_rows",
    "_pair_strings",
    "_unique_horse_names",
    "_kelly_stake",
    "_ticket_market_shrink",
    "_ticket_selection_key",
    "_selection_type_order",
    "_is_coverage_ticket",
    "_is_model_pair_coverage_ticket",
    "_coverage_priority",
    "_prioritize_exotic_types",
    "_probability_band_from_row",
    "_frame_horse_summaries",
    "_resolve_frame_number",
    "_frame_sort_key",
    "_is_top_two_model_pair",
    "_with_long_reason",
    "_horse_summary",
    "_normalize_to_target",
    "_scale_to_target",
    "_estimate_pair_hit_prob",
    "_estimate_market_pair_prob",
    "_wide_pace_adjustment",
    "_estimate_market_pair_odds",
    "_estimate_place_odds",
    "_estimate_predicted_pair_odds",
    "_estimate_predicted_combo_odds",
    "_combo_hit_prob",
    "_ordered_finish_prob",
    "_estimate_exotic_odds",
    "_frame_combo_hit_prob",
    "_frame_quality_adjustment",
    "_single_frame_quality",
    "_canonical_ticket_from_evaluation",
    "_build_top5_wide_box_candidate",
    "_wide_box_outcome_audit",
    "_build_win_ticket",
    "_build_place_ticket",
    "_build_wide_ticket",
    "_build_wakuren_ticket",
    "_build_exotic_ticket",
    "_formation_points",
    "_probability_lineage_failure_plan",
    "_index_odds_history",
    "_ticket_decision_ev",
    "_ticket_decision_probability",
    "_ticket_decision_odds",
    "_rank_ticket_pool",
    "_ticket_type_rank",
    "_limit_eligible_selection_pool",
    "_select_optimized_tickets",
    "_append_ticket_if_new",
    "_would_exceed_horse_dependency",
    "_dedupe_ticket_combos",
    "_calibrate_ticket_probabilities",
    "_portfolio_horse_numbers",
    "_top_win_probability_horse_number",
    "_top_win_probability_horse_numbers",
    "_max_horse_stake_dependency_ratio",
    "_candidate_eligibility_checks",
    "_annotate_candidate_selection",
    "_is_top3_rank_coverage_ticket",
    "_can_add_coverage_ticket",
    "_prune_gami_tickets",
    "_annotate_portfolio_tickets",
    "_candidate_counts_by_type",
    "_labels_for_type",
    "_ticket_combo_label",
    "_reconcile_ticket_classifications",
    "_ticket_horse_numbers_for_classification",
    "_ticket_max_portfolio_stake",
    "_stake_allocation_score",
    "_rebalance_race_stakes",
    "_has_win_standout",
    "_promote_top3_rank_coverage_candidates",
    "_build_place_candidates",
    "_build_wide_candidates",
    "_build_wakuren_candidates",
    "_build_exotic_candidates",
    "_build_sanrentan_formation_candidates",
    "_formation_axis_label",
    "_build_coverage_candidates",
    "_build_model_consistency_candidates",
    "_build_win_ev_translation_candidates",
    "_build_marked_top5_coverage_candidates",
    "_build_race_longshots",
    "_front_value_candidates",
    "_model_score_longshots",
    "_has_longshot_structural_support",
    "_enrich_rows_for_multi_bet",
    "_is_win_ev_translation_row",
    "_win_ev_translation_score",
    "_live_or_current_win_odds",
    "_win_candidate_ev",
    "generate_tickets",
    "_generate_tickets_from_candidate_evaluations",
    "_optimize_portfolio_stakes",
]


def generate_tickets(
    ev_rows: list[dict[str, object]],
    mode: str = "balanced",
    *,
    odds_rows: list[dict[str, object]] | list[dict[str, str]] | None = None,
    odds_history: list[dict[str, object]] | list[dict[str, str]] | None = None,
    candidate_evaluations: list[dict[str, object]] | None = None,
    candidate_validation: dict[str, object] | None = None,
    seconds_to_post: float | None = None,
    bankroll_per_race: int = 1000,
    min_ev: float = 1.03,
    min_place_ev: float = 1.01,
    min_wide_ev: float = 1.01,
    min_wakuren_ev: float = 1.03,
    min_umaren_ev: float = 1.04,
    min_umatan_ev: float = 1.07,
    min_sanrenpuku_ev: float = 1.06,
    min_sanrentan_ev: float = 1.12,
    max_tickets_per_race: int = 5,
    max_wide_tickets_per_race: int = 2,
    max_exotic_tickets_per_race: int = 4,
    kelly_fraction: float = 0.33,
    prefer_wide: bool = False,
    min_portfolio_ev: float = 1.0,
    min_coverage_ev: float = 0.75,
    rank_coverage_enabled: bool = True,
    rank_coverage_budget_yen: int = 300,
    min_rank_coverage_ev: float = 0.90,
    min_rank_coverage_top3_probability: float = 0.55,
    min_rank_coverage_leader_probability: float = 0.25,
    min_rank_coverage_third_vs_fourth_ratio: float = 1.10,
    max_horse_stake_dependency_ratio: float = 0.60,
    top5_wide_box_enabled: bool | None = None,
) -> dict[str, object]:
    if candidate_evaluations is not None:
        if str(dict(candidate_validation or {}).get("status", "")) != "OK":
            return _probability_lineage_failure_plan(candidate_validation)
        return _generate_tickets_from_candidate_evaluations(
            ev_rows,
            candidate_evaluations=candidate_evaluations,
            odds_history=list(odds_history if odds_history is not None else odds_rows or []),
            mode=mode,
            bankroll_per_race=bankroll_per_race,
            min_ev=min_ev,
            min_place_ev=min_place_ev,
            min_wide_ev=min_wide_ev,
            min_wakuren_ev=min_wakuren_ev,
            min_umaren_ev=min_umaren_ev,
            min_umatan_ev=min_umatan_ev,
            min_sanrenpuku_ev=min_sanrenpuku_ev,
            min_sanrentan_ev=min_sanrentan_ev,
            max_tickets_per_race=max_tickets_per_race,
            max_wide_tickets_per_race=max_wide_tickets_per_race,
            max_exotic_tickets_per_race=max_exotic_tickets_per_race,
            kelly_fraction=kelly_fraction,
            prefer_wide=prefer_wide,
            min_portfolio_ev=min_portfolio_ev,
            rank_coverage_enabled=rank_coverage_enabled,
            rank_coverage_budget_yen=rank_coverage_budget_yen,
            min_rank_coverage_ev=min_rank_coverage_ev,
            min_rank_coverage_top3_probability=min_rank_coverage_top3_probability,
            min_rank_coverage_leader_probability=min_rank_coverage_leader_probability,
            min_rank_coverage_third_vs_fourth_ratio=(
                min_rank_coverage_third_vs_fourth_ratio
            ),
            max_horse_stake_dependency_ratio=max_horse_stake_dependency_ratio,
            seconds_to_post=1800.0 if seconds_to_post is None else seconds_to_post,
            top5_wide_box_enabled=(
                mode == "balanced"
                if top5_wide_box_enabled is None
                else top5_wide_box_enabled
            ),
        )

    by_race: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in ev_rows:
        by_race[str(row.get("race_id", ""))].append(row)
    live_odds_by_race = _build_live_odds_lookup(list(odds_rows or []))

    races: list[dict[str, object]] = []
    flat_tickets: list[dict[str, object]] = []
    core: list[dict[str, object]] = []
    partner: list[dict[str, object]] = []
    longshots: list[dict[str, object]] = []
    front_value_candidates: list[dict[str, object]] = []
    candidate_references: list[dict[str, object]] = []
    aggregate_candidate_counts: dict[str, int] = defaultdict(int)

    per_race_limit = max(max_tickets_per_race, 5) if mode == "aggressive" else max_tickets_per_race
    min_win_ev = max(min_ev, MIN_WIN_EV)

    for race_id in sorted(by_race.keys()):
        ranked = sorted(
            by_race[race_id],
            key=lambda row: (_to_float(row.get("ev")), _to_float(row.get("win_prob"))),
            reverse=True,
        )
        enriched = _enrich_rows_for_multi_bet(ranked)
        live_odds = live_odds_by_race.get(race_id, {})
        win_candidates = [
            row
            for row in enriched
            if _win_candidate_ev(row, live_odds) >= min_win_ev
            and _live_or_current_win_odds(row, live_odds) > 0
        ]
        place_candidates = _build_place_candidates(
            enriched,
            bankroll_per_race=bankroll_per_race,
            min_place_ev=min_place_ev,
            kelly_fraction=kelly_fraction,
            live_odds=live_odds,
        )
        wide_candidates = _build_wide_candidates(
            enriched,
            bankroll_per_race=bankroll_per_race,
            min_wide_ev=min_wide_ev,
            kelly_fraction=kelly_fraction,
            live_odds=live_odds,
        )
        wakuren_candidates = _build_wakuren_candidates(
            enriched,
            bankroll_per_race=bankroll_per_race,
            min_wakuren_ev=min_wakuren_ev,
            kelly_fraction=kelly_fraction,
            live_odds=live_odds,
        )
        exotic_candidates = _build_exotic_candidates(
            enriched,
            bankroll_per_race=bankroll_per_race,
            min_umaren_ev=min_umaren_ev,
            min_umatan_ev=min_umatan_ev,
            min_sanrenpuku_ev=min_sanrenpuku_ev,
            min_sanrentan_ev=min_sanrentan_ev,
            kelly_fraction=kelly_fraction,
            live_odds=live_odds,
        )
        coverage_candidates = _build_coverage_candidates(
            enriched,
            bankroll_per_race=bankroll_per_race,
            min_coverage_ev=min_coverage_ev,
            live_odds=live_odds,
        )
        consistency_candidates = _build_model_consistency_candidates(
            enriched,
            bankroll_per_race=bankroll_per_race,
            min_coverage_ev=min_coverage_ev,
            live_odds=live_odds,
        )
        win_ev_translation_candidates = _build_win_ev_translation_candidates(
            enriched,
            bankroll_per_race=bankroll_per_race,
            min_coverage_ev=min_coverage_ev,
            live_odds=live_odds,
        )
        marked_coverage_candidates = _build_marked_top5_coverage_candidates(
            enriched,
            bankroll_per_race=bankroll_per_race,
            min_coverage_ev=min_coverage_ev,
            live_odds=live_odds,
        )

        win_tickets = [
            ticket
            for row in win_candidates
            if (
                ticket := _build_win_ticket(
                    row,
                    bankroll_per_race=bankroll_per_race,
                    kelly_fraction=kelly_fraction,
                    live_odds=live_odds,
                )
            )
            is not None
        ]

        thresholds = {
            "win": min_win_ev,
            "place": min_place_ev,
            "wide": min_wide_ev,
            "wakuren": min_wakuren_ev,
            "umaren": min_umaren_ev,
            "umatan": min_umatan_ev,
            "sanrenpuku": min_sanrenpuku_ev,
            "sanrentan": min_sanrentan_ev,
        }
        all_ticket_candidates = _dedupe_ticket_combos(
            win_tickets
            + place_candidates
            + wide_candidates
            + wakuren_candidates
            + exotic_candidates
            + coverage_candidates
            + consistency_candidates
            + win_ev_translation_candidates
            + marked_coverage_candidates
        )
        calibrated_pool = _calibrate_ticket_probabilities(all_ticket_candidates)
        eligible_pool = [
            ticket
            for ticket in calibrated_pool
            if _to_float(ticket.get("ev_current") or ticket.get("ev"))
            >= (
                min_coverage_ev
                if _is_coverage_ticket(ticket)
                else thresholds.get(str(ticket.get("bet_type", "")), min_ev)
            )
        ]
        selection_pool = _limit_eligible_selection_pool(
            eligible_pool,
            max_wide_tickets_per_race=max_wide_tickets_per_race,
            max_exotic_tickets_per_race=max_exotic_tickets_per_race,
        )
        actionable_win_candidate_numbers = {
            str(row.get("horse_number", "")).strip()
            for row in enriched
            if _win_candidate_ev(row, live_odds)
            >= max(min_win_ev, MIN_ACTIONABLE_WIN_EV)
            and _live_or_current_win_odds(row, live_odds) > 0
        }
        represented_win_candidate_numbers = {
            str(ticket.get("horse_number", "")).strip()
            for ticket in all_ticket_candidates
            if str(ticket.get("bet_type", "")) == "win"
        }
        missing_actionable_win_candidate_numbers = sorted(
            actionable_win_candidate_numbers - represented_win_candidate_numbers,
            key=lambda value: int(_to_float(value, 999.0)),
        )
        if missing_actionable_win_candidate_numbers:
            selection_pool = []
        top_win_horse_number = _top_win_probability_horse_number(enriched)
        stake_dependency_exempt_horse_numbers = _top_win_probability_horse_numbers(
            enriched,
            limit=3,
        )
        race_tickets = _select_optimized_tickets(
            selection_pool,
            per_race_limit=per_race_limit,
            prefer_wide=prefer_wide,
            force_win_standout=_has_win_standout(enriched),
            min_portfolio_ev=min_portfolio_ev,
            required_horse_number=top_win_horse_number,
        )
        race_tickets = _optimize_portfolio_stakes(
            race_tickets,
            bankroll_per_race=bankroll_per_race,
            min_portfolio_ev=min_portfolio_ev,
            max_horse_stake_dependency_ratio=max_horse_stake_dependency_ratio,
            stake_dependency_exempt_horse_numbers=(
                stake_dependency_exempt_horse_numbers
            ),
        )
        race_tickets = _rebalance_race_stakes(race_tickets, bankroll_per_race=bankroll_per_race)
        race_tickets = _annotate_portfolio_tickets(_prune_gami_tickets(race_tickets))
        selection_reason = (
            "actionable_win_candidate_universe_incomplete"
            if missing_actionable_win_candidate_numbers
            else "optimized_portfolio"
        )
        if race_tickets and top_win_horse_number not in _portfolio_horse_numbers(race_tickets):
            race_tickets = []
            selection_reason = "top_win_probability_horse_missing_after_portfolio_checks"
        if not race_tickets and selection_reason == "optimized_portfolio":
            if top_win_horse_number and not any(
                top_win_horse_number in _ticket_horse_numbers(ticket)
                for ticket in selection_pool
            ):
                selection_reason = "top_win_probability_horse_has_no_eligible_ticket"
            else:
                selection_reason = "no_safe_portfolio"

        candidate_pool = _rank_ticket_pool(all_ticket_candidates, prefer_wide=prefer_wide)
        candidate_pool = _annotate_candidate_selection(
            candidate_pool,
            selected_tickets=race_tickets,
            eligible_tickets=eligible_pool,
            selection_pool=selection_pool,
            portfolio_failure_reason=(
                selection_reason if not race_tickets else ""
            ),
        )
        candidate_references.extend(candidate_pool)
        candidate_counts = _candidate_counts_by_type(candidate_pool)
        for bet_type, count in candidate_counts.items():
            aggregate_candidate_counts[bet_type] += count
        flat_tickets.extend(race_tickets)

        place_ranked = sorted(
            enriched,
            key=lambda row: (_to_float(row.get("place_prob")), _to_float(row.get("win_prob"))),
            reverse=True,
        )
        race_core = [_horse_summary(row) for row in place_ranked[:2] if _to_float(row.get("place_prob")) >= 0.22]
        race_partner = [_horse_summary(row) for row in place_ranked[2:4] if _to_float(row.get("place_prob")) >= 0.16]
        race_long = _build_race_longshots(enriched, min_win_ev=min_win_ev, limit=2)
        race_front_value_candidates = _front_value_candidates(enriched)
        race_core, race_partner, race_long = _reconcile_ticket_classifications(
            race_core,
            race_partner,
            race_long,
            race_tickets,
            enriched,
        )

        core.extend(race_core)
        partner.extend(race_partner)
        longshots.extend(race_long)
        front_value_candidates.extend(race_front_value_candidates)
        races.append(
            {
                "race_id": race_id,
                "core": race_core,
                "partner": race_partner,
                "long": race_long,
                "front_value_candidates": race_front_value_candidates,
                "candidate_counts": candidate_counts,
                "candidates": candidate_pool,
                "exotics": exotic_candidates[:max_exotic_tickets_per_race],
                "tickets": race_tickets,
                "portfolio": _portfolio_summary(race_tickets),
                "selection_status": "selected" if race_tickets else "no_bet",
                "selection_reason": selection_reason,
                "top_win_probability_horse_number": top_win_horse_number,
                "horse_stake_dependency_ratio": _fmt(
                    _max_horse_stake_dependency_ratio(
                        race_tickets,
                        exempt_horse_numbers=stake_dependency_exempt_horse_numbers,
                    )
                ),
                "max_horse_stake_dependency_ratio": _fmt(
                    max_horse_stake_dependency_ratio
                ),
                "horse_dependency_enforced": False,
                "horse_stake_dependency_scope": "outside_top3_win_probability",
                "actionable_win_candidate_numbers": sorted(
                    actionable_win_candidate_numbers,
                    key=lambda value: int(_to_float(value, 999.0)),
                ),
                "missing_actionable_win_candidate_numbers": (
                    missing_actionable_win_candidate_numbers
                ),
            }
        )

    combo_seed = _unique_horse_names(core + partner)
    fukusho_labels = (
        _labels_for_type(flat_tickets, "place")
        or _labels_for_type(candidate_references, "place")
        or [str(item.get("horse_name", "")) for item in core[:2] if str(item.get("horse_name", "")).strip()]
    )
    wakuren_labels = _labels_for_type(flat_tickets, "wakuren") or _labels_for_type(candidate_references, "wakuren")
    umaren_labels = (
        _labels_for_type(flat_tickets, "umaren")
        or _labels_for_type(candidate_references, "umaren")
        or _pair_strings(combo_seed[:3])
    )
    umatan_labels = _labels_for_type(flat_tickets, "umatan") or _labels_for_type(candidate_references, "umatan")
    sanrenpuku_labels = (
        _labels_for_type(flat_tickets, "sanrenpuku")
        or _labels_for_type(candidate_references, "sanrenpuku")
        or ([" - ".join(combo_seed[:3])] if len(combo_seed) >= 3 else [])
    )
    sanrentan_labels = (
        _labels_for_type(flat_tickets, "sanrentan")
        or _labels_for_type(candidate_references, "sanrentan")
        or ([" → ".join(combo_seed[:3])] if len(combo_seed) >= 3 else [])
    )

    return {
        "core": core,
        "partner": partner,
        "long": longshots,
        "front_value_candidates": front_value_candidates,
        "tickets": flat_tickets,
        "races": races,
        "bet_types_considered": ["win", "place", "wide", "wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan"],
        "candidate_counts": dict(aggregate_candidate_counts),
        "optimization_mode": "cross_bet_ev_kelly_portfolio",
        "portfolio_summary": _portfolio_summary(flat_tickets),
        "primary_bet_type": flat_tickets[0].get("bet_type", "wide") if flat_tickets else "wide",
        "tansho": [ticket.get("horse_name", "") for ticket in flat_tickets if ticket.get("bet_type") == "win"][:2],
        "fukusho": fukusho_labels[:3],
        "wide": [ticket.get("horse_name", "") for ticket in flat_tickets if ticket.get("bet_type") == "wide"][:3]
        or _pair_strings([item.get("horse_name", "") for item in core[:3]]),
        "wakuren": wakuren_labels[:3],
        "umaren": umaren_labels[:3],
        "umatan": umatan_labels[:3],
        "sanrenpuku": sanrenpuku_labels[:3],
        "sanrentan": sanrentan_labels[:3],
    }

def _generate_tickets_from_candidate_evaluations(
    ev_rows: list[dict[str, object]],
    *,
    candidate_evaluations: list[dict[str, object]],
    odds_history: list[dict[str, object]] | list[dict[str, str]],
    mode: str,
    bankroll_per_race: int,
    min_ev: float,
    min_place_ev: float,
    min_wide_ev: float,
    min_wakuren_ev: float,
    min_umaren_ev: float,
    min_umatan_ev: float,
    min_sanrenpuku_ev: float,
    min_sanrentan_ev: float,
    max_tickets_per_race: int,
    max_wide_tickets_per_race: int,
    max_exotic_tickets_per_race: int,
    kelly_fraction: float,
    prefer_wide: bool,
    min_portfolio_ev: float,
    rank_coverage_enabled: bool,
    rank_coverage_budget_yen: int,
    min_rank_coverage_ev: float,
    min_rank_coverage_top3_probability: float,
    min_rank_coverage_leader_probability: float,
    min_rank_coverage_third_vs_fourth_ratio: float,
    max_horse_stake_dependency_ratio: float,
    seconds_to_post: float,
    top5_wide_box_enabled: bool,
) -> dict[str, object]:
    thresholds = {
        "win": max(min_ev, MIN_WIN_EV),
        "place": min_place_ev,
        "wide": min_wide_ev,
        "wakuren": min_wakuren_ev,
        "umaren": min_umaren_ev,
        "umatan": min_umatan_ev,
        "sanrenpuku": min_sanrenpuku_ev,
        "sanrentan": min_sanrentan_ev,
    }
    minimum_probabilities = {
        "win": 0.04,
        "place": 0.16,
        "wide": 0.10,
        "wakuren": 0.035,
        "umaren": 0.035,
        "umatan": 0.018,
        "sanrenpuku": 0.018,
        "sanrentan": 0.006,
    }
    rows_by_race: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in ev_rows:
        rows_by_race[str(row.get("race_id", ""))].append(dict(row))
    evaluations_by_race: dict[str, list[dict[str, object]]] = defaultdict(list)
    seen_keys: set[str] = set()
    lineage_errors: list[str] = []
    for evaluation in candidate_evaluations:
        row = dict(evaluation)
        race_id = str(row.get("race_id", "")).strip()
        bet_type = str(row.get("bet_type", "")).strip()
        try:
            combination = canonical_combination(
                bet_type,
                row.get("canonical_combination", ""),
            )
        except ValueError as exc:
            lineage_errors.append(str(exc))
            continue
        expected_key = f"{race_id}|{bet_type}|{combination}"
        if str(row.get("key", "")) != expected_key or expected_key in seen_keys:
            lineage_errors.append(f"invalid or duplicate candidate key: {expected_key}")
            continue
        probability = _to_float(row.get("hit_prob"))
        odds = _to_float(row.get("official_odds"))
        if probability <= 0 or probability > 1 or odds <= 1:
            lineage_errors.append(f"invalid candidate probability or odds: {expected_key}")
            continue
        if abs(_to_float(row.get("ev")) - (probability * odds)) > 1e-9:
            lineage_errors.append(f"candidate EV mismatch: {expected_key}")
            continue
        seen_keys.add(expected_key)
        row["canonical_combination"] = combination
        evaluations_by_race[race_id].append(row)
    if lineage_errors:
        return _probability_lineage_failure_plan(
            {"status": "NG", "errors": lineage_errors}
        )

    races: list[dict[str, object]] = []
    flat_tickets: list[dict[str, object]] = []
    all_candidates: list[dict[str, object]] = []
    core: list[dict[str, object]] = []
    partner: list[dict[str, object]] = []
    longshots: list[dict[str, object]] = []
    front_value_candidates: list[dict[str, object]] = []
    aggregate_counts: dict[str, int] = defaultdict(int)
    per_race_limit = max(max_tickets_per_race, 5) if mode == "aggressive" else max_tickets_per_race
    rank_coverage_budget = max(0, min(rank_coverage_budget_yen, bankroll_per_race))
    odds_history_index = _index_odds_history(odds_history)

    for race_id in sorted(rows_by_race):
        race_rows = rows_by_race[race_id]
        row_by_number = {
            str(int(_to_float(row.get("horse_number")))): row
            for row in race_rows
            if _to_float(row.get("horse_number")) > 0
        }
        candidate_pool = [
            _canonical_ticket_from_evaluation(
                evaluation,
                row_by_number=row_by_number,
                odds_history=odds_history_index.get(
                    (
                        str(evaluation.get("race_id", "")).strip(),
                        str(evaluation.get("bet_type", "")).strip(),
                        str(evaluation.get("canonical_combination", "")).strip(),
                    ),
                    [],
                ),
                bankroll_per_race=bankroll_per_race,
                kelly_fraction=kelly_fraction,
                seconds_to_post=seconds_to_post,
                min_ev=thresholds[str(evaluation.get("bet_type", ""))],
            )
            for evaluation in evaluations_by_race.get(race_id, [])
        ]
        if top5_wide_box_enabled:
            wide_box = _build_top5_wide_box_candidate(
                [ticket for ticket in candidate_pool if ticket],
                race_rows,
                bankroll_per_race=bankroll_per_race,
            )
            if wide_box is not None:
                candidate_pool.append(wide_box)
        rank_coverage = _promote_top3_rank_coverage_candidates(
            [ticket for ticket in candidate_pool if ticket],
            race_rows,
            enabled=rank_coverage_enabled,
            budget_yen=rank_coverage_budget,
            min_robust_ev=min_rank_coverage_ev,
            min_top3_probability=min_rank_coverage_top3_probability,
            min_leader_probability=min_rank_coverage_leader_probability,
            min_third_vs_fourth_ratio=min_rank_coverage_third_vs_fourth_ratio,
        )
        candidate_pool = [
            {
                **ticket,
                "eligibility_checks": _candidate_eligibility_checks(
                    ticket,
                    minimum_ev=(
                        min_rank_coverage_ev
                        if _is_top3_rank_coverage_ticket(ticket)
                        else thresholds[str(ticket.get("bet_type", ""))]
                    ),
                    minimum_hit_probability=minimum_probabilities[str(ticket.get("bet_type", ""))],
                ),
            }
            for ticket in candidate_pool
            if ticket
        ]
        eligible_pool = [
            ticket
            for ticket in candidate_pool
            if all(check["passed"] for check in ticket["eligibility_checks"])
        ]
        selection_pool = _limit_eligible_selection_pool(
            eligible_pool,
            max_wide_tickets_per_race=max_wide_tickets_per_race,
            max_exotic_tickets_per_race=max_exotic_tickets_per_race,
        )
        top_horse = _top_win_probability_horse_number(race_rows)
        top3 = _top_win_probability_horse_numbers(race_rows, limit=3)
        race_tickets = _select_optimized_tickets(
            selection_pool,
            per_race_limit=per_race_limit,
            prefer_wide=prefer_wide,
            force_win_standout=False,
            min_portfolio_ev=min_portfolio_ev,
            required_horse_number=top_horse,
        )
        race_tickets = _optimize_portfolio_stakes(
            race_tickets,
            bankroll_per_race=bankroll_per_race,
            min_portfolio_ev=min_portfolio_ev,
            max_horse_stake_dependency_ratio=max_horse_stake_dependency_ratio,
            stake_dependency_exempt_horse_numbers=top3,
        )
        race_tickets = _rebalance_race_stakes(race_tickets, bankroll_per_race=bankroll_per_race)
        race_tickets = _annotate_portfolio_tickets(_prune_gami_tickets(race_tickets))
        if race_tickets and top_horse not in _portfolio_horse_numbers(race_tickets):
            race_tickets = []
        annotated = _annotate_candidate_selection(
            _rank_ticket_pool(candidate_pool, prefer_wide=prefer_wide),
            selected_tickets=race_tickets,
            eligible_tickets=eligible_pool,
            selection_pool=selection_pool,
            portfolio_failure_reason="" if race_tickets else "no_safe_robust_portfolio",
        )
        counts = _candidate_counts_by_type(candidate_pool)
        for bet_type, count in counts.items():
            aggregate_counts[bet_type] += count
        all_candidates.extend(annotated)
        flat_tickets.extend(race_tickets)

        place_ranked = sorted(
            race_rows,
            key=lambda row: (_to_float(row.get("place_prob")), _to_float(row.get("win_prob"))),
            reverse=True,
        )
        race_core = [_horse_summary(row) for row in place_ranked[:2]]
        race_partner = [_horse_summary(row) for row in place_ranked[2:4]]
        race_long = _build_race_longshots(race_rows, min_win_ev=thresholds["win"], limit=2)
        race_front_value_candidates = _front_value_candidates(race_rows)
        race_core, race_partner, race_long = _reconcile_ticket_classifications(
            race_core,
            race_partner,
            race_long,
            race_tickets,
            race_rows,
        )
        core.extend(race_core)
        partner.extend(race_partner)
        longshots.extend(race_long)
        front_value_candidates.extend(race_front_value_candidates)
        races.append(
            {
                "race_id": race_id,
                "core": race_core,
                "partner": race_partner,
                "long": race_long,
                "front_value_candidates": race_front_value_candidates,
                "candidate_counts": counts,
                "candidates": annotated,
                "tickets": race_tickets,
                "portfolio": _portfolio_summary(race_tickets),
                "selection_status": "selected" if race_tickets else "no_bet",
                "selection_reason": "robust_portfolio" if race_tickets else "no_safe_robust_portfolio",
                "top_win_probability_horse_number": top_horse,
                "rank_coverage": rank_coverage,
            }
        )

    return {
        "core": core,
        "partner": partner,
        "long": longshots,
        "front_value_candidates": front_value_candidates,
        "tickets": flat_tickets,
        "races": races,
        "bet_types_considered": list(BET_TYPE_MARKET_SHRINK),
        "candidate_counts": dict(aggregate_counts),
        "candidate_evaluations": all_candidates,
        "optimization_mode": "canonical_probability_robust_odds",
        "probability_lineage": {
            "status": "OK",
            "source": "04_ev_calculator",
            "candidate_count": len(candidate_evaluations),
            "tolerance": PROBABILITY_SUM_TOLERANCE,
        },
        "portfolio_summary": _portfolio_summary(flat_tickets),
        "primary_bet_type": flat_tickets[0].get("bet_type", "wide") if flat_tickets else "wide",
        "rank_coverage": {
            "enabled": rank_coverage_enabled,
            "budget_yen": rank_coverage_budget,
            "ticket_count": sum(
                1 for ticket in flat_tickets if _is_top3_rank_coverage_ticket(ticket)
            ),
            "stake": sum(
                int(_to_float(ticket.get("stake")))
                for ticket in flat_tickets
                if _is_top3_rank_coverage_ticket(ticket)
            ),
        },
    }


def _optimize_portfolio_stakes(
    tickets: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_portfolio_ev: float,
    max_horse_stake_dependency_ratio: float = 0.60,
    stake_dependency_exempt_horse_numbers: set[str] | None = None,
) -> list[dict[str, object]]:
    # Kept in the signature for configuration compatibility. Horse dependency
    # is now audit-only and must not reject or cap an otherwise valid portfolio.
    _ = max_horse_stake_dependency_ratio, stake_dependency_exempt_horse_numbers
    if not tickets or bankroll_per_race <= 0:
        return []

    allocated: list[dict[str, object]] = []
    for ticket in tickets:
        unit = _ticket_stake_unit(ticket)
        stake = int(_to_float(ticket.get("stake"), 0.0))
        base_stake = max(unit, int(stake / unit) * unit)
        if base_stake <= bankroll_per_race:
            allocated.append(_with_adjusted_stake(ticket, base_stake))

    if not allocated:
        return []
    if _portfolio_total_stake(allocated) > bankroll_per_race:
        allocated = _rebalance_race_stakes(allocated, bankroll_per_race=bankroll_per_race)

    while allocated:
        remaining = bankroll_per_race - _portfolio_total_stake(allocated)
        if remaining < 100:
            break

        best_idx = -1
        best_score = 0.0
        best_trial: list[dict[str, object]] | None = None
        for idx, ticket in enumerate(allocated):
            unit = _ticket_stake_unit(ticket)
            if unit > remaining:
                continue

            current_stake = int(_to_float(ticket.get("stake"), 0.0))
            max_stake = _ticket_max_portfolio_stake(ticket, bankroll_per_race=bankroll_per_race)
            if current_stake + unit > max_stake:
                continue

            trial_ticket = _with_adjusted_stake(ticket, current_stake + unit)
            trial = [dict(item) for item in allocated]
            trial[idx] = trial_ticket
            if _portfolio_ev(trial) < min_portfolio_ev:
                continue
            if _portfolio_no_gami(allocated) and not _portfolio_no_gami(trial):
                continue
            score = _stake_allocation_score(ticket)
            if score > best_score:
                best_idx = idx
                best_score = score
                best_trial = trial

        if best_idx < 0 or best_trial is None:
            return allocated
        allocated = best_trial

    return allocated
