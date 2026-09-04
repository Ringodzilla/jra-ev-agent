from __future__ import annotations

from collections import defaultdict
from itertools import combinations, permutations
import math

from strategy.live_odds import (
    build_live_odds_lookup as _build_live_odds_lookup,
    live_odds_value as _live_odds_value,
    lookup_live_odds as _lookup_live_odds,
)
from strategy.portfolio import (
    is_formation_ticket as _is_formation_ticket,
    portfolio_ev as _portfolio_ev,
    portfolio_expected_return as _portfolio_expected_return,
    portfolio_no_gami as _portfolio_no_gami,
    portfolio_summary as _portfolio_summary,
    portfolio_total_points as _portfolio_total_points,
    portfolio_total_stake as _portfolio_total_stake,
    ticket_max_return_if_hit as _ticket_max_return_if_hit,
    ticket_point_count as _ticket_point_count,
    ticket_return_if_hit as _ticket_return_if_hit,
    ticket_stake_unit as _ticket_stake_unit,
    with_adjusted_stake as _with_adjusted_stake,
)


MIN_WIN_EV = 1.05
MIN_ACTIONABLE_WIN_EV = 1.08
BET_TYPE_MARKET_SHRINK = {
    "win": 0.35,
    "place": 0.45,
    "wide": 0.55,
    "wakuren": 0.60,
    "umaren": 0.62,
    "umatan": 0.70,
    "sanrenpuku": 0.76,
    "sanrentan": 0.82,
}


def generate_tickets(
    ev_rows: list[dict[str, object]],
    mode: str = "balanced",
    *,
    odds_rows: list[dict[str, object]] | list[dict[str, str]] | None = None,
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
    max_horse_stake_dependency_ratio: float = 0.60,
) -> dict[str, object]:
    by_race: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in ev_rows:
        by_race[str(row.get("race_id", ""))].append(row)
    live_odds_by_race = _build_live_odds_lookup(list(odds_rows or []))

    races: list[dict[str, object]] = []
    flat_tickets: list[dict[str, object]] = []
    core: list[dict[str, object]] = []
    partner: list[dict[str, object]] = []
    longshots: list[dict[str, object]] = []
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
        if (
            race_tickets
            and _max_horse_stake_dependency_ratio(
                race_tickets,
                exempt_horse_numbers=stake_dependency_exempt_horse_numbers,
            )
            > max_horse_stake_dependency_ratio
        ):
            race_tickets = []
            selection_reason = "horse_stake_dependency_limit_exceeded"
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
        races.append(
            {
                "race_id": race_id,
                "core": race_core,
                "partner": race_partner,
                "long": race_long,
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


def _build_win_ticket(
    row: dict[str, object],
    *,
    bankroll_per_race: int,
    kelly_fraction: float,
    live_odds: dict[tuple[str, str], dict[str, object]] | None = None,
) -> dict[str, object] | None:
    prob = _to_float(row.get("win_prob"))
    live = _lookup_live_odds(live_odds or {}, "win", [str(row.get("horse_number", ""))])
    odds = _live_odds_value(live) or _to_float(row.get("current_odds"))
    ev = prob * odds if odds > 0 else _to_float(row.get("ev"))
    if prob <= 0 or odds <= 1.0 or ev < MIN_WIN_EV:
        return None

    full_kelly = ((odds * prob) - 1.0) / max(odds - 1.0, 1e-6)
    recommended_fraction = max(0.0, min(0.30, full_kelly * kelly_fraction))
    stake = int((bankroll_per_race * recommended_fraction) / 100) * 100
    if stake < 100:
        stake = 100 if ev >= MIN_ACTIONABLE_WIN_EV else 0
    if stake <= 0:
        return None

    return {
        "race_id": str(row.get("race_id", "")),
        "bet_type": "win",
        "horse_id": str(row.get("horse_id", "")),
        "horse_name": str(row.get("horse_name", "")),
        "horse_number": int(_to_float(row.get("horse_number"), 0.0)),
        "stake": stake,
        "win_prob": _fmt(prob),
        "win_odds": _fmt(odds),
        "ev": _fmt(ev),
        "ev_current": _fmt(ev),
        "ev_predicted": str(row.get("ev_predicted", "")),
        "fair_odds": str(row.get("fair_odds", "")),
        "model_score": str(row.get("model_score", "")),
        "consistency": str(row.get("consistency", "")),
        "history_count": str(row.get("history_count", "")),
        "probability_band": str(row.get("probability_band") or _probability_band_from_row(row)),
        "market_shrink_used": str(row.get("market_shrink_used", "")),
        "predicted_odds": str(row.get("predicted_odds", "")),
        "predicted_odds_source": "jra_live" if live else str(row.get("predicted_odds_source", "")),
        "odds_source": "jra_live" if live else "entry",
    }


def _build_place_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_place_ev: float,
    kelly_fraction: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    tickets: list[dict[str, object]] = []
    for row in rows:
        ticket = _build_place_ticket(
            row,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=min(0.42, kelly_fraction + 0.05),
            min_place_ev=min_place_ev,
            live_odds=live_odds,
        )
        if ticket is not None:
            tickets.append(ticket)

    tickets.sort(
        key=lambda ticket: (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob")),
            _to_float(ticket.get("confidence")),
        ),
        reverse=True,
    )
    return tickets


def _build_place_ticket(
    row: dict[str, object],
    *,
    bankroll_per_race: int,
    kelly_fraction: float,
    min_place_ev: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> dict[str, object] | None:
    place_prob = _to_float(row.get("place_prob"))
    market_place_prob = _to_float(row.get("market_place_prob"))
    if place_prob < 0.16 or market_place_prob <= 0:
        return None

    live = _lookup_live_odds(live_odds, "place", [str(row.get("horse_number", ""))])
    current_odds_est = _live_odds_value(live) or _estimate_place_odds(market_place_prob)
    predicted_odds_est = _estimate_predicted_combo_odds([row], current_odds_est=current_odds_est, max_odds=18.0)
    ev_current = place_prob * current_odds_est if current_odds_est > 0 else 0.0
    ev_predicted = place_prob * predicted_odds_est if predicted_odds_est > 0 else 0.0
    if current_odds_est <= 1.0 or ev_current < min_place_ev:
        return None

    stake = _kelly_stake(
        probability=place_prob,
        odds=current_odds_est,
        bankroll_per_race=bankroll_per_race,
        kelly_fraction=kelly_fraction,
        min_ev=min_place_ev,
        max_fraction=0.32,
    )
    if stake <= 0:
        return None

    confidence = (place_prob / max(market_place_prob, 1e-6)) if market_place_prob > 0 else 0.0
    return {
        "race_id": str(row.get("race_id", "")),
        "bet_type": "place",
        "horse_id": str(row.get("horse_id", "")),
        "horse_name": str(row.get("horse_name", "")),
        "horse_number": int(_to_float(row.get("horse_number"), 0.0)),
        "stake": stake,
        "hit_prob": _fmt(place_prob),
        "place_prob": _fmt(place_prob),
        "place_prob_market": _fmt(market_place_prob),
        "win_prob": str(row.get("win_prob", "")),
        "win_odds": _fmt(current_odds_est),
        "place_odds_est": _fmt(current_odds_est),
        "place_odds_min": str(live.get("odds_min", "")) if live else "",
        "place_odds_max": str(live.get("odds_max", "")) if live else "",
        "predicted_odds": _fmt(predicted_odds_est),
        "ev": _fmt(ev_current),
        "ev_current": _fmt(ev_current),
        "ev_predicted": _fmt(ev_predicted),
        "fair_odds": str(row.get("place_fair_odds", "")),
        "model_score": str(row.get("model_score", "")),
        "predicted_odds_source": "jra_live" if live else "place_estimated",
        "odds_source": "jra_live" if live else "estimated",
        "confidence": _fmt(confidence),
        "legs": [
            {
                "horse_id": str(row.get("horse_id", "")),
                "horse_name": str(row.get("horse_name", "")),
                "horse_number": str(row.get("horse_number", "")),
                "win_prob": str(row.get("win_prob", "")),
                "place_prob": _fmt(place_prob),
            }
        ],
    }


def _build_wide_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_wide_ev: float,
    kelly_fraction: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    if len(rows) < 2:
        return []

    field_size = len(rows)
    pool = sorted(
        rows,
        key=lambda row: (
            _to_float(row.get("place_prob")),
            _to_float(row.get("ev_predicted") or row.get("ev")),
            _to_float(row.get("win_prob")),
        ),
        reverse=True,
    )[:5]

    pairs: list[dict[str, object]] = []
    for left_idx in range(len(pool)):
        for right_idx in range(left_idx + 1, len(pool)):
            ticket = _build_wide_ticket(
                pool[left_idx],
                pool[right_idx],
                field_size=field_size,
                bankroll_per_race=bankroll_per_race,
                kelly_fraction=kelly_fraction,
                min_wide_ev=min_wide_ev,
                live_odds=live_odds,
            )
            if ticket is not None:
                pairs.append(ticket)

    pairs.sort(
        key=lambda ticket: (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob")),
            _to_float(ticket.get("confidence")),
        ),
        reverse=True,
    )
    return pairs


def _build_wide_ticket(
    left: dict[str, object],
    right: dict[str, object],
    *,
    field_size: int,
    bankroll_per_race: int,
    kelly_fraction: float,
    min_wide_ev: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
    ticket_role: str = "value",
    coverage_reason: str = "",
    require_live_odds: bool = False,
    allow_flat_stake: bool = False,
    min_pair_prob: float = 0.10,
) -> dict[str, object] | None:
    pair_prob = _estimate_pair_hit_prob(left, right, field_size=field_size)
    market_pair_prob = _estimate_market_pair_prob(left, right, field_size=field_size)
    horse_numbers = [str(left.get("horse_number", "")), str(right.get("horse_number", ""))]
    live = _lookup_live_odds(live_odds, "wide", horse_numbers)
    if require_live_odds and not live:
        return None
    current_odds_est = _live_odds_value(live) or _estimate_market_pair_odds(market_pair_prob)
    predicted_odds_est = _estimate_predicted_pair_odds(left, right, current_odds_est=current_odds_est)
    pace_adjustment = _wide_pace_adjustment(left, right)
    pair_prob *= pace_adjustment
    ev_current = pair_prob * current_odds_est if current_odds_est > 0 else 0.0
    ev_predicted = pair_prob * predicted_odds_est if predicted_odds_est > 0 else 0.0

    if pair_prob < min_pair_prob or current_odds_est <= 1.0 or ev_current < min_wide_ev:
        return None

    stake = _kelly_stake(
        probability=pair_prob,
        odds=current_odds_est,
        bankroll_per_race=bankroll_per_race,
        kelly_fraction=min(0.40, kelly_fraction + 0.05),
        min_ev=min_wide_ev,
        max_fraction=0.36,
    )
    if stake <= 0:
        if allow_flat_stake and current_odds_est > 1.0 and ev_current >= min_wide_ev:
            stake = min(100, bankroll_per_race)
        else:
            return None

    horse_ids = [str(left.get("horse_id", "")), str(right.get("horse_id", ""))]
    horse_names = [str(left.get("horse_name", "")), str(right.get("horse_name", ""))]
    confidence = (pair_prob / max(market_pair_prob, 1e-6)) if market_pair_prob > 0 else 0.0

    return {
        "race_id": str(left.get("race_id", "")),
        "bet_type": "wide",
        "horse_id": "|".join(horse_ids),
        "horse_name": " - ".join(horse_names),
        "horse_number": "-".join(horse_numbers),
        "horse_ids": horse_ids,
        "horse_names": horse_names,
        "horse_numbers": horse_numbers,
        "stake": stake,
        "hit_prob": _fmt(pair_prob),
        "win_prob": _fmt(pair_prob),
        "wide_prob": _fmt(pair_prob),
        "wide_prob_market": _fmt(market_pair_prob),
        "win_odds": _fmt(current_odds_est),
        "wide_odds_est": _fmt(current_odds_est),
        "wide_odds_min": str(live.get("odds_min", "")) if live else "",
        "wide_odds_max": str(live.get("odds_max", "")) if live else "",
        "predicted_odds": _fmt(predicted_odds_est),
        "predicted_wide_odds": _fmt(predicted_odds_est),
        "ev": _fmt(ev_current),
        "ev_current": _fmt(ev_current),
        "ev_predicted": _fmt(ev_predicted),
        "predicted_odds_source": "jra_live" if live else "pair_estimated",
        "odds_source": "jra_live" if live else "estimated",
        "ticket_role": ticket_role,
        "coverage_reason": coverage_reason,
        "confidence": _fmt(confidence),
        "pace_adjustment": _fmt(pace_adjustment),
        "legs": [
            {
                "horse_id": horse_ids[0],
                "horse_name": horse_names[0],
                "horse_number": horse_numbers[0],
                "place_prob": str(left.get("place_prob", "")),
                "win_prob": str(left.get("win_prob", "")),
            },
            {
                "horse_id": horse_ids[1],
                "horse_name": horse_names[1],
                "horse_number": horse_numbers[1],
                "place_prob": str(right.get("place_prob", "")),
                "win_prob": str(right.get("win_prob", "")),
            },
        ],
    }


def _build_wakuren_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_wakuren_ev: float,
    kelly_fraction: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    if len(rows) < 2:
        return []

    field_size = len(rows)
    by_frame: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        frame_number = _resolve_frame_number(row, field_size=field_size)
        if not frame_number:
            continue
        normalized = dict(row)
        normalized["frame_number"] = frame_number
        by_frame[frame_number].append(normalized)

    has_multi_horse_frame = any(len(frame_rows) >= 2 for frame_rows in by_frame.values())
    if field_size < 9 and not has_multi_horse_frame:
        return []
    if len(by_frame) < 2 and not has_multi_horse_frame:
        return []

    frame_numbers = sorted(by_frame.keys(), key=_frame_sort_key)
    tickets: list[dict[str, object]] = []
    for left_idx, left_frame in enumerate(frame_numbers):
        for right_frame in frame_numbers[left_idx:]:
            if left_frame == right_frame and len(by_frame[left_frame]) < 2:
                continue
            ticket = _build_wakuren_ticket(
                left_frame,
                by_frame[left_frame],
                right_frame,
                by_frame[right_frame],
                bankroll_per_race=bankroll_per_race,
                kelly_fraction=min(kelly_fraction, 0.24),
                min_wakuren_ev=min_wakuren_ev,
                live_odds=live_odds,
            )
            if ticket is not None:
                tickets.append(ticket)

    tickets.sort(
        key=lambda ticket: (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob")),
            _to_float(ticket.get("confidence")),
        ),
        reverse=True,
    )
    return tickets


def _build_wakuren_ticket(
    left_frame: str,
    left_rows: list[dict[str, object]],
    right_frame: str,
    right_rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    kelly_fraction: float,
    min_wakuren_ev: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> dict[str, object] | None:
    hit_prob = _frame_combo_hit_prob(left_rows, right_rows, same_frame=left_frame == right_frame, key="win_prob")
    market_prob = _frame_combo_hit_prob(left_rows, right_rows, same_frame=left_frame == right_frame, key="market_prob")
    frame_quality_adjustment = _frame_quality_adjustment(left_rows, right_rows, same_frame=left_frame == right_frame)
    hit_prob *= frame_quality_adjustment
    if hit_prob < 0.035 or market_prob <= 0:
        return None

    frame_numbers = [left_frame, right_frame]
    live = _lookup_live_odds(live_odds, "wakuren", frame_numbers)
    current_odds_est = _live_odds_value(live) or _estimate_exotic_odds(market_prob, payout_rate=0.775, max_odds=150.0)
    prediction_rows = left_rows if left_frame == right_frame else left_rows + right_rows
    predicted_odds_est = _estimate_predicted_combo_odds(
        prediction_rows,
        current_odds_est=current_odds_est,
        max_odds=150.0,
    )
    ev_current = hit_prob * current_odds_est if current_odds_est > 0 else 0.0
    ev_predicted = hit_prob * predicted_odds_est if predicted_odds_est > 0 else 0.0
    if current_odds_est <= 1.0 or ev_current < min_wakuren_ev:
        return None

    stake = _kelly_stake(
        probability=hit_prob,
        odds=current_odds_est,
        bankroll_per_race=bankroll_per_race,
        kelly_fraction=kelly_fraction,
        min_ev=min_wakuren_ev,
        max_fraction=0.18,
    )
    if stake <= 0:
        return None

    confidence = (hit_prob / max(market_prob, 1e-6)) if market_prob > 0 else 0.0
    return {
        "race_id": str((left_rows or right_rows)[0].get("race_id", "")),
        "bet_type": "wakuren",
        "horse_id": f"frame:{left_frame}|frame:{right_frame}",
        "horse_name": f"{left_frame}枠 - {right_frame}枠",
        "horse_number": "-".join(frame_numbers),
        "frame_numbers": frame_numbers,
        "stake": stake,
        "hit_prob": _fmt(hit_prob),
        "win_prob": _fmt(hit_prob),
        "combo_prob": _fmt(hit_prob),
        "combo_prob_market": _fmt(market_prob),
        "win_odds": _fmt(current_odds_est),
        "wakuren_odds_est": _fmt(current_odds_est),
        "predicted_odds": _fmt(predicted_odds_est),
        "ev": _fmt(ev_current),
        "ev_current": _fmt(ev_current),
        "ev_predicted": _fmt(ev_predicted),
        "predicted_odds_source": "jra_live" if live else "wakuren_estimated",
        "odds_source": "jra_live" if live else "estimated",
        "confidence": _fmt(confidence),
        "frame_quality_adjustment": _fmt(frame_quality_adjustment),
        "legs": [
            {
                "frame_number": left_frame,
                "horses": _frame_horse_summaries(left_rows),
            },
            {
                "frame_number": right_frame,
                "horses": _frame_horse_summaries(right_rows),
            },
        ],
    }


def _build_exotic_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_umaren_ev: float,
    min_umatan_ev: float,
    min_sanrenpuku_ev: float,
    min_sanrentan_ev: float,
    kelly_fraction: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    if len(rows) < 2:
        return []

    pool = sorted(
        rows,
        key=lambda row: (
            _to_float(row.get("win_prob")),
            _to_float(row.get("place_prob")),
            _to_float(row.get("ev_predicted") or row.get("ev")),
        ),
        reverse=True,
    )[:6]

    candidates: list[dict[str, object]] = []
    for combo in combinations(pool, 2):
        ticket = _build_exotic_ticket(
            list(combo),
            bet_type="umaren",
            payout_rate=0.775,
            max_odds=120.0,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=min(kelly_fraction, 0.25),
            min_ev=min_umaren_ev,
            min_prob=0.035,
            max_fraction=0.20,
            live_odds=live_odds,
        )
        if ticket is not None:
            candidates.append(ticket)

    for order in permutations(pool, 2):
        ticket = _build_exotic_ticket(
            list(order),
            bet_type="umatan",
            payout_rate=0.75,
            max_odds=300.0,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=min(kelly_fraction, 0.22),
            min_ev=min_umatan_ev,
            min_prob=0.018,
            max_fraction=0.16,
            live_odds=live_odds,
        )
        if ticket is not None:
            candidates.append(ticket)

    if len(pool) >= 3:
        for combo in combinations(pool[:5], 3):
            ticket = _build_exotic_ticket(
                list(combo),
                bet_type="sanrenpuku",
                payout_rate=0.775,
                max_odds=240.0,
                bankroll_per_race=bankroll_per_race,
                kelly_fraction=min(kelly_fraction, 0.20),
                min_ev=min_sanrenpuku_ev,
                min_prob=0.018,
                max_fraction=0.14,
                live_odds=live_odds,
            )
            if ticket is not None:
                candidates.append(ticket)

        ordered_pool = pool[:4]
        for order in permutations(ordered_pool, 3):
            ticket = _build_exotic_ticket(
                list(order),
                bet_type="sanrentan",
                payout_rate=0.725,
                max_odds=600.0,
                bankroll_per_race=bankroll_per_race,
                kelly_fraction=min(kelly_fraction, 0.16),
                min_ev=min_sanrentan_ev,
                min_prob=0.006,
                max_fraction=0.10,
                live_odds=live_odds,
            )
            if ticket is not None:
                candidates.append(ticket)

        candidates.extend(
            _build_sanrentan_formation_candidates(
                pool,
                bankroll_per_race=bankroll_per_race,
                min_sanrentan_ev=min_sanrentan_ev,
                live_odds=live_odds,
            )
        )

    candidates.sort(
        key=lambda ticket: (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob")),
            _to_float(ticket.get("confidence")),
        ),
        reverse=True,
    )

    return _prioritize_exotic_types(_dedupe_ticket_combos(candidates))


def _build_sanrentan_formation_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_sanrentan_ev: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    if len(rows) < 3:
        return []

    ranked = sorted(
        rows,
        key=lambda row: (
            _to_float(row.get("win_prob")),
            _to_float(row.get("place_prob")),
            _to_float(row.get("ev_predicted") or row.get("ev")),
        ),
        reverse=True,
    )
    max_points = max(1, bankroll_per_race // 100)
    specs = [
        (ranked[:1], ranked[:3], ranked[:5]),
        (ranked[:2], ranked[:3], ranked[:4]),
        (ranked[:1], ranked[:4], ranked[:5]),
    ]

    candidates: list[dict[str, object]] = []
    seen_shapes: set[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = set()
    for first_rows, second_rows, third_rows in specs:
        if not first_rows or not second_rows or not third_rows:
            continue
        shape_key = (
            tuple(str(row.get("horse_number", "")) for row in first_rows),
            tuple(str(row.get("horse_number", "")) for row in second_rows),
            tuple(str(row.get("horse_number", "")) for row in third_rows),
        )
        if shape_key in seen_shapes:
            continue
        seen_shapes.add(shape_key)

        points = _formation_points(
            first_rows,
            second_rows,
            third_rows,
            live_odds=live_odds,
        )
        point_count = len(points)
        if point_count < 2 or point_count > max_points:
            continue

        total_prob = sum(_to_float(point.get("hit_prob")) for point in points)
        total_market_prob = sum(_to_float(point.get("market_prob")) for point in points)
        expected_return_multiplier = sum(
            _to_float(point.get("hit_prob")) * _to_float(point.get("odds"))
            for point in points
        )
        formation_ev = expected_return_multiplier / point_count
        if total_prob < 0.006 or formation_ev < min_sanrentan_ev:
            continue

        stake_per_point = 100
        stake = point_count * stake_per_point
        if stake > bankroll_per_race:
            continue

        all_rows = _dedupe_rows_by_horse_number(first_rows + second_rows + third_rows)
        horse_ids = [str(row.get("horse_id", "")) for row in all_rows]
        horse_names = [str(row.get("horse_name", "")) for row in all_rows]
        horse_numbers = [str(row.get("horse_number", "")) for row in all_rows]
        confidence = (total_prob / max(total_market_prob, 1e-6)) if total_market_prob > 0 else 0.0
        min_odds = min(_to_float(point.get("odds")) for point in points)
        max_odds = max(_to_float(point.get("odds")) for point in points)
        avg_odds = (expected_return_multiplier / max(total_prob, 1e-6)) if total_prob > 0 else 0.0

        candidates.append(
            {
                "race_id": str(rows[0].get("race_id", "")),
                "bet_type": "sanrentan",
                "ticket_shape": "formation",
                "horse_id": "|".join(horse_ids),
                "horse_name": _formation_axis_label(first_rows, second_rows, third_rows, key="horse_name"),
                "horse_number": _formation_axis_label(first_rows, second_rows, third_rows, key="horse_number"),
                "horse_ids": horse_ids,
                "horse_names": horse_names,
                "horse_numbers": horse_numbers,
                "stake": stake,
                "stake_per_point": stake_per_point,
                "point_count": point_count,
                "hit_prob": _fmt(total_prob),
                "win_prob": _fmt(total_prob),
                "combo_prob": _fmt(total_prob),
                "combo_prob_market": _fmt(total_market_prob),
                "win_odds": _fmt(avg_odds),
                "trifecta_odds_est": _fmt(avg_odds),
                "trifecta_odds_min": _fmt(min_odds),
                "trifecta_odds_max": _fmt(max_odds),
                "predicted_odds": _fmt(avg_odds),
                "ev": _fmt(formation_ev),
                "ev_current": _fmt(formation_ev),
                "ev_predicted": _fmt(formation_ev),
                "predicted_odds_source": "jra_live" if all(point.get("odds_source") == "jra_live" for point in points) else "sanrentan_formation",
                "odds_source": "jra_live" if all(point.get("odds_source") == "jra_live" for point in points) else "estimated",
                "ticket_role": "value",
                "coverage_reason": "",
                "confidence": _fmt(confidence),
                "formation_ev_basis": "total_points",
                "formation": {
                    "first": _formation_axis_rows(first_rows),
                    "second": _formation_axis_rows(second_rows),
                    "third": _formation_axis_rows(third_rows),
                    "point_count": point_count,
                },
                "points": points,
                "min_return_if_hit": int(stake_per_point * min_odds),
                "max_return_if_hit": int(stake_per_point * max_odds),
                "legs": [
                    {
                        "horse_id": str(row.get("horse_id", "")),
                        "horse_name": str(row.get("horse_name", "")),
                        "horse_number": str(row.get("horse_number", "")),
                        "win_prob": str(row.get("win_prob", "")),
                        "place_prob": str(row.get("place_prob", "")),
                    }
                    for row in all_rows
                ],
            }
        )

    candidates.sort(
        key=lambda ticket: (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob")),
            _to_float(ticket.get("confidence")),
        ),
        reverse=True,
    )
    return candidates


def _formation_points(
    first_rows: list[dict[str, object]],
    second_rows: list[dict[str, object]],
    third_rows: list[dict[str, object]],
    *,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    for first in first_rows:
        for second in second_rows:
            for third in third_rows:
                point_rows = [first, second, third]
                numbers = [str(row.get("horse_number", "")) for row in point_rows]
                if len(set(numbers)) != 3:
                    continue

                hit_prob = _combo_hit_prob(point_rows, key="win_prob", bet_type="sanrentan")
                market_prob = _combo_hit_prob(point_rows, key="market_prob", bet_type="sanrentan")
                if hit_prob <= 0 or market_prob <= 0:
                    continue

                live = _lookup_live_odds(live_odds, "sanrentan", numbers)
                odds = _live_odds_value(live) or _estimate_exotic_odds(market_prob, payout_rate=0.725, max_odds=600.0)
                if odds <= 1.0:
                    continue

                point_ev = hit_prob * odds
                points.append(
                    {
                        "horse_number": _combo_name(numbers, bet_type="sanrentan"),
                        "horse_name": _combo_name([str(row.get("horse_name", "")) for row in point_rows], bet_type="sanrentan"),
                        "horse_numbers": numbers,
                        "hit_prob": _fmt(hit_prob),
                        "market_prob": _fmt(market_prob),
                        "odds": _fmt(odds),
                        "ev": _fmt(point_ev),
                        "odds_source": "jra_live" if live else "estimated",
                    }
                )
    return points


def _build_coverage_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_coverage_ev: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    """Build high-conviction saver tickets, but only with real odds and an EV floor."""
    if len(rows) < 3:
        return []

    core = sorted(
        rows,
        key=lambda row: (
            _to_float(row.get("win_prob")),
            _to_float(row.get("place_prob")),
            _to_float(row.get("ev_predicted") or row.get("ev")),
        ),
        reverse=True,
    )[:3]
    if len(core) < 3:
        return []

    specs = [
        ("umaren", core[:2], 0.775, 120.0, 0.020),
        ("umatan", core[:2], 0.750, 300.0, 0.012),
        ("sanrenpuku", core, 0.775, 240.0, 0.010),
        ("sanrentan", core, 0.725, 600.0, 0.004),
    ]

    tickets: list[dict[str, object]] = []
    for bet_type, combo_rows, payout_rate, max_odds, min_prob in specs:
        ticket = _build_exotic_ticket(
            list(combo_rows),
            bet_type=bet_type,
            payout_rate=payout_rate,
            max_odds=max_odds,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=0.0,
            min_ev=min_coverage_ev,
            min_prob=min_prob,
            max_fraction=0.08,
            live_odds=live_odds,
            ticket_role="coverage",
            require_live_odds=True,
            allow_flat_stake=True,
        )
        if ticket is not None:
            tickets.append(ticket)

    tickets.sort(
        key=lambda ticket: (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob")),
            _to_float(ticket.get("win_odds")),
        ),
        reverse=True,
    )
    return _dedupe_ticket_combos(tickets)


def _build_model_consistency_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_coverage_ev: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    """Keep model-top pairs in the betting surface when real wide odds exist."""
    if len(rows) < 3:
        return []

    field_size = len(rows)
    core = sorted(
        rows,
        key=lambda row: (
            _to_float(row.get("win_prob")),
            _to_float(row.get("place_prob")),
            _to_float(row.get("ev_predicted") or row.get("ev")),
        ),
        reverse=True,
    )[:3]
    if len(core) < 3:
        return []

    top2_sum = sum(_to_float(row.get("win_prob")) for row in core[:2])
    top3_sum = sum(_to_float(row.get("win_prob")) for row in core)
    if top2_sum < 0.28 and top3_sum < 0.38:
        return []

    tickets: list[dict[str, object]] = []
    for left, right in combinations(core, 2):
        ticket = _build_wide_ticket(
            left,
            right,
            field_size=field_size,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=0.0,
            min_wide_ev=min(min_coverage_ev, 0.55),
            live_odds=live_odds,
            ticket_role="coverage",
            coverage_reason="top_model_pair_real_odds",
            require_live_odds=True,
            allow_flat_stake=True,
            min_pair_prob=0.08,
        )
        if ticket is not None:
            tickets.append(ticket)

    tickets.sort(
        key=lambda ticket: (
            _is_top_two_model_pair(ticket, core),
            _to_float(ticket.get("hit_prob")),
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
        ),
        reverse=True,
    )
    return _dedupe_ticket_combos(tickets)


def _build_win_ev_translation_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_coverage_ev: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    """Translate high win-EV outsiders into place/wide coverage candidates."""
    if len(rows) < 3:
        return []

    field_size = len(rows)
    ranked_by_win_ev = sorted(
        rows,
        key=lambda row: (
            _win_ev_translation_score(row),
            _to_float(row.get("win_prob")),
            _to_float(row.get("current_odds")),
        ),
        reverse=True,
    )
    value_rows = [
        row
        for row in ranked_by_win_ev[: max(3, min(6, len(ranked_by_win_ev)))]
        if _is_win_ev_translation_row(row)
    ][:2]
    if not value_rows:
        return []

    anchors = sorted(
        rows,
        key=lambda row: (
            _to_float(row.get("place_prob")),
            _to_float(row.get("win_prob")),
            _to_float(row.get("market_prob")),
        ),
        reverse=True,
    )[:5]

    tickets: list[dict[str, object]] = []
    for value_row in value_rows:
        place_ticket = _build_place_ticket(
            value_row,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=0.0,
            min_place_ev=min(min_coverage_ev, 0.74),
            live_odds=live_odds,
        )
        if place_ticket is not None:
            place_ticket = dict(place_ticket)
            place_ticket["ticket_role"] = "coverage"
            place_ticket["coverage_reason"] = "win_ev_longshot_place_translation"
            place_ticket["stake"] = min(int(_to_float(place_ticket.get("stake"), 100.0)), 100)
            tickets.append(place_ticket)

        for anchor in anchors:
            if str(anchor.get("horse_number", "")) == str(value_row.get("horse_number", "")):
                continue
            ticket = _build_wide_ticket(
                value_row,
                anchor,
                field_size=field_size,
                bankroll_per_race=bankroll_per_race,
                kelly_fraction=0.0,
                min_wide_ev=min(min_coverage_ev, 0.74),
                live_odds=live_odds,
                ticket_role="coverage",
                coverage_reason="win_ev_longshot_wide_translation",
                require_live_odds=True,
                allow_flat_stake=True,
                min_pair_prob=0.025,
            )
            if ticket is not None:
                tickets.append(ticket)

    tickets.sort(
        key=lambda ticket: (
            str(ticket.get("coverage_reason", "")).endswith("wide_translation"),
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob") or ticket.get("win_prob")),
        ),
        reverse=True,
    )
    return _dedupe_ticket_combos(tickets)


def _build_marked_top5_coverage_candidates(
    rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_coverage_ev: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    """Keep the model's marked top five in the real-odds betting surface."""
    if len(rows) < 3:
        return []

    field_size = len(rows)
    marked = sorted(
        rows,
        key=lambda row: (
            _to_float(row.get("place_prob")),
            _to_float(row.get("win_prob")),
            _to_float(row.get("ev_predicted") or row.get("ev")),
        ),
        reverse=True,
    )[:5]
    if len(marked) < 3:
        return []

    tickets: list[dict[str, object]] = []
    marked_core = marked[:4]
    marked_core_index = {id(row): index for index, row in enumerate(marked_core)}
    for left, right in combinations(marked_core, 2):
        if max(marked_core_index[id(left)], marked_core_index[id(right)]) <= 2:
            continue
        ticket = _build_wide_ticket(
            left,
            right,
            field_size=field_size,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=0.0,
            min_wide_ev=min(min_coverage_ev, 0.62),
            live_odds=live_odds,
            ticket_role="coverage",
            coverage_reason="marked_core_pair_real_odds",
            require_live_odds=True,
            allow_flat_stake=True,
            min_pair_prob=0.038,
        )
        if ticket is not None:
            tickets.append(ticket)

    for combo in combinations(marked_core, 3):
        ticket = _build_exotic_ticket(
            list(combo),
            bet_type="sanrenpuku",
            payout_rate=0.775,
            max_odds=220.0,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=0.0,
            min_ev=min(min_coverage_ev, 0.62),
            min_prob=0.0045,
            max_fraction=0.06,
            live_odds=live_odds,
            ticket_role="coverage",
            require_live_odds=True,
            allow_flat_stake=True,
            coverage_reason="marked_core_trio_real_odds",
        )
        if ticket is not None:
            tickets.append(ticket)

    for left, right in combinations(marked, 2):
        ticket = _build_wide_ticket(
            left,
            right,
            field_size=field_size,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=0.0,
            min_wide_ev=min(min_coverage_ev, 0.70),
            live_odds=live_odds,
            ticket_role="coverage",
            coverage_reason="marked_top5_pair_real_odds",
            require_live_odds=True,
            allow_flat_stake=True,
            min_pair_prob=0.045,
        )
        if ticket is not None:
            tickets.append(ticket)

    for combo in combinations(marked, 3):
        ticket = _build_exotic_ticket(
            list(combo),
            bet_type="sanrenpuku",
            payout_rate=0.775,
            max_odds=240.0,
            bankroll_per_race=bankroll_per_race,
            kelly_fraction=0.0,
            min_ev=min(min_coverage_ev, 0.70),
            min_prob=0.006,
            max_fraction=0.08,
            live_odds=live_odds,
            ticket_role="coverage",
            require_live_odds=True,
            allow_flat_stake=True,
            coverage_reason="marked_top5_trio_real_odds",
        )
        if ticket is not None:
            tickets.append(ticket)

    tickets.sort(
        key=lambda ticket: (
            str(ticket.get("bet_type", "")) == "sanrenpuku",
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob") or ticket.get("win_prob")),
        ),
        reverse=True,
    )
    return _dedupe_ticket_combos(tickets)


def _build_exotic_ticket(
    combo_rows: list[dict[str, object]],
    *,
    bet_type: str,
    payout_rate: float,
    max_odds: float,
    bankroll_per_race: int,
    kelly_fraction: float,
    min_ev: float,
    min_prob: float,
    max_fraction: float,
    live_odds: dict[tuple[str, str], dict[str, object]],
    ticket_role: str = "value",
    require_live_odds: bool = False,
    allow_flat_stake: bool = False,
    coverage_reason: str = "",
) -> dict[str, object] | None:
    hit_prob = _combo_hit_prob(combo_rows, key="win_prob", bet_type=bet_type)
    market_prob = _combo_hit_prob(combo_rows, key="market_prob", bet_type=bet_type)
    if hit_prob < min_prob or market_prob <= 0:
        return None

    horse_numbers = [str(row.get("horse_number", "")) for row in combo_rows]
    live = _lookup_live_odds(live_odds, bet_type, horse_numbers)
    if require_live_odds and not live:
        return None
    current_odds_est = _live_odds_value(live) or _estimate_exotic_odds(market_prob, payout_rate=payout_rate, max_odds=max_odds)
    predicted_odds_est = _estimate_predicted_combo_odds(combo_rows, current_odds_est=current_odds_est, max_odds=max_odds)
    ev_current = hit_prob * current_odds_est if current_odds_est > 0 else 0.0
    ev_predicted = hit_prob * predicted_odds_est if predicted_odds_est > 0 else 0.0
    if current_odds_est <= 1.0 or ev_current < min_ev:
        return None

    stake = _kelly_stake(
        probability=hit_prob,
        odds=current_odds_est,
        bankroll_per_race=bankroll_per_race,
        kelly_fraction=kelly_fraction,
        min_ev=min_ev,
        max_fraction=max_fraction,
    )
    if stake <= 0:
        if allow_flat_stake and current_odds_est > 1.0 and ev_current >= min_ev:
            stake = min(100, bankroll_per_race)
        else:
            return None

    horse_ids = [str(row.get("horse_id", "")) for row in combo_rows]
    horse_names = [str(row.get("horse_name", "")) for row in combo_rows]
    confidence = (hit_prob / max(market_prob, 1e-6)) if market_prob > 0 else 0.0
    odds_key = {
        "umaren": "umaren_odds_est",
        "umatan": "umatan_odds_est",
        "sanrenpuku": "trio_odds_est",
        "sanrentan": "trifecta_odds_est",
    }[bet_type]

    return {
        "race_id": str(combo_rows[0].get("race_id", "")),
        "bet_type": bet_type,
        "horse_id": "|".join(horse_ids),
        "horse_name": _combo_name(horse_names, bet_type=bet_type),
        "horse_number": _combo_name(horse_numbers, bet_type=bet_type),
        "horse_ids": horse_ids,
        "horse_names": horse_names,
        "horse_numbers": horse_numbers,
        "stake": stake,
        "hit_prob": _fmt(hit_prob),
        "win_prob": _fmt(hit_prob),
        "combo_prob": _fmt(hit_prob),
        "combo_prob_market": _fmt(market_prob),
        "win_odds": _fmt(current_odds_est),
        odds_key: _fmt(current_odds_est),
        "predicted_odds": _fmt(predicted_odds_est),
        "ev": _fmt(ev_current),
        "ev_current": _fmt(ev_current),
        "ev_predicted": _fmt(ev_predicted),
        "predicted_odds_source": "jra_live" if live else f"{bet_type}_estimated",
        "odds_source": "jra_live" if live else "estimated",
        "ticket_role": ticket_role,
        "coverage_reason": coverage_reason or ("top_model_combo_real_odds" if ticket_role == "coverage" else ""),
        "confidence": _fmt(confidence),
        "legs": [
            {
                "horse_id": horse_id,
                "horse_name": horse_name,
                "horse_number": horse_number,
                "win_prob": str(row.get("win_prob", "")),
                "place_prob": str(row.get("place_prob", "")),
            }
            for horse_id, horse_name, horse_number, row in zip(horse_ids, horse_names, horse_numbers, combo_rows)
        ],
    }


def _horse_summary(row: dict[str, object]) -> dict[str, object]:
    return {
        "race_id": str(row.get("race_id", "")),
        "horse_id": str(row.get("horse_id", "")),
        "horse_name": str(row.get("horse_name", "")),
        "horse_number": str(row.get("horse_number", "")),
        "win_prob": str(row.get("win_prob", "")),
        "place_prob": str(row.get("place_prob", "")),
        "ev": str(row.get("ev", "")),
        "ev_predicted": str(row.get("ev_predicted", "")),
        "current_odds": str(row.get("current_odds", "")),
        "predicted_odds": str(row.get("predicted_odds", "")),
        "predicted_odds_source": str(row.get("predicted_odds_source", "")),
        "model_score": str(row.get("model_score", "")),
        "probability_band": str(row.get("probability_band") or _probability_band_from_row(row)),
        "long_reason": str(row.get("long_reason", "")),
    }


def _build_race_longshots(
    rows: list[dict[str, object]],
    *,
    min_win_ev: float,
    limit: int,
) -> list[dict[str, object]]:
    if not rows or limit <= 0:
        return []

    value_longs = [
        _with_long_reason(row, "win_ev_threshold")
        for row in rows
        if _to_float(row.get("current_odds")) >= 10.0
        and _to_float(row.get("ev")) >= max(min_win_ev, MIN_ACTIONABLE_WIN_EV)
    ]
    model_longs = _model_score_longshots(rows)
    merged = _dedupe_rows_by_horse_number(value_longs + model_longs)
    merged.sort(
        key=lambda row: (
            str(row.get("long_reason")) == "top_model_score_longshot",
            _to_float(row.get("ev")),
            _to_float(row.get("model_score")),
            _to_float(row.get("win_prob")),
        ),
        reverse=True,
    )
    return [_horse_summary(row) for row in merged[:limit]]


def _reconcile_ticket_classifications(
    core: list[dict[str, object]],
    partner: list[dict[str, object]],
    longshots: list[dict[str, object]],
    tickets: list[dict[str, object]],
    rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    """Keep classifications exclusive and cover every horse used by a ticket."""
    seen: set[str] = set()

    def unique(items: list[dict[str, object]]) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for item in items:
            horse_number = str(item.get("horse_number", "")).strip()
            if not horse_number or horse_number in seen:
                continue
            seen.add(horse_number)
            out.append(item)
        return out

    core = unique(core)
    partner = unique(partner)
    longshots = unique(longshots)
    rows_by_number = {str(row.get("horse_number", "")).strip(): row for row in rows}
    ticket_numbers: set[str] = set()
    for ticket in tickets:
        ticket_numbers.update(_ticket_horse_numbers_for_classification(ticket))

    for horse_number in sorted(ticket_numbers, key=lambda value: int(value) if value.isdigit() else math.inf):
        if horse_number in seen or horse_number not in rows_by_number:
            continue
        summary = _horse_summary(rows_by_number[horse_number])
        summary["long_reason"] = "selected_ticket"
        longshots.append(summary)
        seen.add(horse_number)
    return core, partner, longshots


def _ticket_horse_numbers_for_classification(ticket: dict[str, object]) -> set[str]:
    numbers = {str(value).strip() for value in list(ticket.get("horse_numbers") or []) if str(value).strip()}
    for leg in list(ticket.get("legs") or []):
        if not isinstance(leg, dict):
            continue
        horse_number = str(leg.get("horse_number", "")).strip()
        if horse_number:
            numbers.add(horse_number)
        for horse in list(leg.get("horses") or []):
            if isinstance(horse, dict):
                horse_number = str(horse.get("horse_number", "")).strip()
                if horse_number:
                    numbers.add(horse_number)
    if not numbers and str(ticket.get("bet_type", "")) != "wakuren":
        numbers.update(_ticket_horse_numbers(ticket))
    return numbers


def _model_score_longshots(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    ranked = sorted(rows, key=lambda row: _to_float(row.get("model_score")), reverse=True)
    if not ranked:
        return []

    top_score = _to_float(ranked[0].get("model_score"))
    top_window = ranked[: max(1, math.ceil(len(ranked) * 0.20))]
    out: list[dict[str, object]] = []
    for index, row in enumerate(top_window):
        score = _to_float(row.get("model_score"))
        odds = _to_float(row.get("current_odds"))
        band = str(row.get("probability_band") or _probability_band_from_row(row))
        if index > 0 and score < top_score * 0.92:
            continue
        if odds < 10.0 or band not in {"outsider", "longshot"}:
            continue
        if not _has_longshot_structural_support(row):
            continue
        out.append(_with_long_reason(row, "top_model_score_longshot"))
    return out[:2]


def _has_longshot_structural_support(row: dict[str, object]) -> bool:
    return (
        _to_float(row.get("weight_score")) >= 0.50
        or _to_float(row.get("pace_score")) >= 0.38
        or _to_float(row.get("course_score")) >= 0.38
    )


def _with_long_reason(row: dict[str, object], reason: str) -> dict[str, object]:
    out = dict(row)
    out["long_reason"] = reason
    return out


def _dedupe_rows_by_horse_number(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for row in rows:
        horse_number = str(row.get("horse_number", "")).strip()
        if not horse_number or horse_number in seen:
            continue
        seen.add(horse_number)
        out.append(row)
    return out


def _formation_axis_rows(rows: list[dict[str, object]]) -> list[dict[str, str]]:
    return [
        {
            "horse_id": str(row.get("horse_id", "")),
            "horse_name": str(row.get("horse_name", "")),
            "horse_number": str(row.get("horse_number", "")),
        }
        for row in rows
    ]


def _formation_axis_label(
    first_rows: list[dict[str, object]],
    second_rows: list[dict[str, object]],
    third_rows: list[dict[str, object]],
    *,
    key: str,
) -> str:
    return (
        f"1着:{_axis_values(first_rows, key)} / "
        f"2着:{_axis_values(second_rows, key)} / "
        f"3着:{_axis_values(third_rows, key)}"
    )


def _axis_values(rows: list[dict[str, object]], key: str) -> str:
    values = [str(row.get(key, "")).strip() for row in rows if str(row.get(key, "")).strip()]
    return ",".join(values)


def _pair_strings(names: list[str]) -> list[str]:
    cleaned = [name for name in names if name]
    out: list[str] = []
    for i in range(len(cleaned)):
        for j in range(i + 1, len(cleaned)):
            out.append(f"{cleaned[i]} - {cleaned[j]}")
    return out


def _unique_horse_names(rows: list[dict[str, object]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        name = str(row.get("horse_name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _enrich_rows_for_multi_bet(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    if not rows:
        return []

    field_size = len(rows)
    target_hits = 2.0 if field_size <= 7 else 3.0
    raw_place: list[float] = []
    market_place: list[float] = []
    floors: list[float] = []

    for row in rows:
        win_prob = _to_float(row.get("win_prob"))
        market_prob = _to_float(row.get("market_prob"))
        consistency = _to_float(row.get("consistency"), 0.5)
        history = min(_to_float(row.get("history_count"), 0.0) / 5.0, 1.0)
        band = str(row.get("probability_band") or _probability_band_from_row(row))
        band_boost = {
            "favorite": 1.60,
            "contender": 1.82,
            "outsider": 2.04,
            "longshot": 2.18,
        }.get(band, 1.80)
        market_boost = {
            "favorite": 1.72,
            "contender": 1.92,
            "outsider": 2.10,
            "longshot": 2.28,
        }.get(band, 1.90)

        raw_place.append(
            max(
                win_prob,
                (win_prob * band_boost)
                + (0.18 * consistency)
                + (0.08 * history)
                + (0.08 * market_prob * target_hits),
            )
        )
        market_place.append(
            max(
                market_prob,
                (market_prob * market_boost)
                + (0.10 * consistency)
                + (0.05 * history),
            )
        )
        floors.append(win_prob)

    caps = [0.82 if field_size > 7 else 0.72 for _ in rows]
    place_probs = _normalize_to_target(raw_place, target=target_hits, floors=floors, caps=caps)
    market_place_probs = _normalize_to_target(
        market_place,
        target=target_hits,
        floors=[_to_float(row.get("market_prob")) for row in rows],
        caps=caps,
    )

    enriched: list[dict[str, object]] = []
    for row, place_prob, market_place_prob in zip(rows, place_probs, market_place_probs):
        out = dict(row)
        out["place_prob"] = _fmt(max(_to_float(row.get("win_prob")), place_prob))
        out["market_place_prob"] = _fmt(market_place_prob)
        out["place_fair_odds"] = _fmt((1.0 / place_prob) if place_prob > 0 else 0.0)
        out["place_edge"] = _fmt(place_prob - market_place_prob)
        enriched.append(out)
    return enriched


def _normalize_to_target(
    values: list[float],
    *,
    target: float,
    floors: list[float],
    caps: list[float],
) -> list[float]:
    if not values:
        return []

    normalized = _scale_to_target(values, target)
    out = [
        _clamp(value, minimum=max(0.0, floor), maximum=max(max(0.0, floor), cap))
        for value, floor, cap in zip(normalized, floors, caps)
    ]

    for _ in range(8):
        total = sum(out)
        delta = target - total
        if abs(delta) <= 1e-6:
            break

        if delta > 0:
            adjustable = [idx for idx, (value, cap) in enumerate(zip(out, caps)) if value < cap - 1e-9]
            if not adjustable:
                break
            weights = [max(values[idx], 1e-6) for idx in adjustable]
            weight_total = sum(weights) or float(len(adjustable))
            for idx, weight in zip(adjustable, weights):
                add = delta * (weight / weight_total)
                out[idx] = min(caps[idx], out[idx] + add)
        else:
            adjustable = [idx for idx, (value, floor) in enumerate(zip(out, floors)) if value > floor + 1e-9]
            if not adjustable:
                break
            weights = [max(out[idx] - floors[idx], 1e-6) for idx in adjustable]
            weight_total = sum(weights) or float(len(adjustable))
            remove = abs(delta)
            for idx, weight in zip(adjustable, weights):
                cut = remove * (weight / weight_total)
                out[idx] = max(floors[idx], out[idx] - cut)

    return out


def _scale_to_target(values: list[float], target: float) -> list[float]:
    total = sum(max(0.0, value) for value in values)
    if total <= 0:
        equal = target / max(len(values), 1)
        return [equal for _ in values]
    return [(max(0.0, value) / total) * target for value in values]


def _estimate_pair_hit_prob(
    left: dict[str, object],
    right: dict[str, object],
    *,
    field_size: int,
) -> float:
    slots = 2.0 if field_size <= 7 else 3.0
    left_place = _to_float(left.get("place_prob"))
    right_place = _to_float(right.get("place_prob"))
    avg_consistency = (_to_float(left.get("consistency"), 0.5) + _to_float(right.get("consistency"), 0.5)) / 2.0
    front_gap = abs(_to_float(left.get("front_rate"), 0.5) - _to_float(right.get("front_rate"), 0.5))
    complement = 1.0 - min(front_gap, 1.0)
    inflation = 1.14 + (0.55 * (slots / max(field_size, 1))) + (0.12 * avg_consistency) + (0.08 * complement)
    joint = left_place * right_place * inflation
    return _clamp(joint, minimum=0.0, maximum=min(left_place, right_place) * 0.97)


def _estimate_market_pair_prob(
    left: dict[str, object],
    right: dict[str, object],
    *,
    field_size: int,
) -> float:
    slots = 2.0 if field_size <= 7 else 3.0
    left_place = _to_float(left.get("market_place_prob"))
    right_place = _to_float(right.get("market_place_prob"))
    inflation = 1.10 + (0.45 * (slots / max(field_size, 1)))
    joint = left_place * right_place * inflation
    return _clamp(joint, minimum=0.0, maximum=min(left_place, right_place) * 0.98)


def _wide_pace_adjustment(left: dict[str, object], right: dict[str, object]) -> float:
    high_mix = max(_to_float(left.get("pace_mix_high")), _to_float(right.get("pace_mix_high")))
    if high_mix < 0.45:
        return 1.0

    left_front = _to_float(left.get("front_rate"), 0.5)
    right_front = _to_float(right.get("front_rate"), 0.5)
    if left_front < 0.62 or right_front < 0.62:
        return 1.0

    avg_closing = (_to_float(left.get("closing_strength")) + _to_float(right.get("closing_strength"))) / 2.0
    if avg_closing < 0.35:
        return 0.72
    if avg_closing < 0.50:
        return 0.84
    return 0.92


def _estimate_market_pair_odds(market_pair_prob: float) -> float:
    if market_pair_prob <= 0:
        return 0.0
    return _clamp(0.82 / market_pair_prob, minimum=1.1, maximum=75.0)


def _estimate_place_odds(market_place_prob: float) -> float:
    if market_place_prob <= 0:
        return 0.0
    return _clamp(0.80 / market_place_prob, minimum=1.1, maximum=18.0)


def _estimate_predicted_pair_odds(
    left: dict[str, object],
    right: dict[str, object],
    *,
    current_odds_est: float,
) -> float:
    return _estimate_predicted_combo_odds([left, right], current_odds_est=current_odds_est, max_odds=max(1.1, current_odds_est * 1.18))


def _estimate_predicted_combo_odds(
    rows: list[dict[str, object]],
    *,
    current_odds_est: float,
    max_odds: float,
) -> float:
    ratios: list[float] = []
    for row in rows:
        current = _to_float(row.get("current_odds"))
        predicted = _to_float(row.get("predicted_odds"))
        if current > 0 and predicted > 0:
            ratios.append(predicted / current)

    trend_ratio = math.prod(ratios) ** (1.0 / len(ratios)) if ratios else 1.0
    trend_ratio = _clamp(trend_ratio, minimum=0.88, maximum=1.16)
    return _clamp(current_odds_est * trend_ratio, minimum=1.1, maximum=min(max_odds, max(1.1, current_odds_est * 1.18)))


def _combo_hit_prob(rows: list[dict[str, object]], *, key: str, bet_type: str) -> float:
    if len(rows) < 2:
        return 0.0
    if bet_type in {"umatan", "sanrentan"}:
        return _ordered_finish_prob(rows, key=key)
    return sum(_ordered_finish_prob(list(order), key=key) for order in permutations(rows, len(rows)))


def _ordered_finish_prob(rows: list[dict[str, object]], *, key: str) -> float:
    remaining = 1.0
    probability = 1.0
    for row in rows:
        horse_prob = _to_float(row.get(key))
        if horse_prob <= 0 or remaining <= 0 or horse_prob >= remaining:
            return 0.0
        probability *= horse_prob / remaining
        remaining -= horse_prob
    return probability


def _estimate_exotic_odds(market_prob: float, *, payout_rate: float, max_odds: float) -> float:
    if market_prob <= 0:
        return 0.0
    return _clamp(payout_rate / market_prob, minimum=1.1, maximum=max_odds)


def _combo_name(values: list[str], *, bet_type: str) -> str:
    separator = " → " if bet_type in {"umatan", "sanrentan"} else " - "
    return separator.join(values)


def _ticket_combo_label(ticket: dict[str, object]) -> str:
    if _is_formation_ticket(ticket):
        return str(ticket.get("horse_number", ""))
    if ticket.get("bet_type") == "wakuren":
        frame_numbers = [str(value) for value in list(ticket.get("frame_numbers") or []) if str(value).strip()]
        return "-".join(frame_numbers) if frame_numbers else str(ticket.get("horse_number", ""))
    values = list(ticket.get("horse_names") or [])
    if not values:
        return str(ticket.get("horse_name", ""))
    return _combo_name([str(value) for value in values], bet_type=str(ticket.get("bet_type", "")))


def _dedupe_ticket_combos(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    index_by_key: dict[tuple[str, str, str], int] = {}
    out: list[dict[str, object]] = []
    for ticket in tickets:
        key = (
            str(ticket.get("race_id", "")),
            str(ticket.get("bet_type", "")),
            str(ticket.get("horse_number", "")),
        )
        if key in seen:
            existing = out[index_by_key[key]]
            if _coverage_priority(ticket) > _coverage_priority(existing):
                out[index_by_key[key]] = ticket
            continue
        seen.add(key)
        index_by_key[key] = len(out)
        out.append(ticket)
    return out


def _coverage_priority(ticket: dict[str, object]) -> int:
    reason = str(ticket.get("coverage_reason", ""))
    if reason in {"marked_core_pair_real_odds", "marked_core_trio_real_odds"}:
        return 4
    if reason in {
        "top_model_pair_real_odds",
        "top_model_combo_real_odds",
        "win_ev_longshot_place_translation",
        "win_ev_longshot_wide_translation",
    }:
        return 3
    if reason in {"marked_top5_pair_real_odds", "marked_top5_trio_real_odds"}:
        return 2
    return 0


def _prioritize_exotic_types(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    by_type: dict[str, list[dict[str, object]]] = defaultdict(list)
    for ticket in tickets:
        by_type[str(ticket.get("bet_type", ""))].append(ticket)

    out: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    for bet_type in ("umaren", "umatan", "sanrenpuku", "sanrentan"):
        if by_type[bet_type]:
            ticket = by_type[bet_type][0]
            out.append(ticket)
            seen_ids.add(id(ticket))

    for ticket in tickets:
        if id(ticket) in seen_ids:
            continue
        out.append(ticket)
    return out


def _rank_ticket_pool(tickets: list[dict[str, object]], *, prefer_wide: bool) -> list[dict[str, object]]:
    return sorted(
        tickets,
        key=lambda ticket: (
            _ticket_type_rank(str(ticket.get("bet_type", "")), prefer_wide=prefer_wide),
            -_to_float(ticket.get("ev_current") or ticket.get("ev")),
            -_to_float(ticket.get("hit_prob") or ticket.get("win_prob")),
            -_to_float(ticket.get("confidence")),
        ),
    )


def _limit_eligible_selection_pool(
    tickets: list[dict[str, object]],
    *,
    max_wide_tickets_per_race: int,
    max_exotic_tickets_per_race: int,
) -> list[dict[str, object]]:
    """Apply type caps only after calibration so rejected tickets can be refilled."""
    def calibrated_rank(ticket: dict[str, object]) -> tuple[float, float, float]:
        return (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob") or ticket.get("win_prob")),
            _to_float(ticket.get("confidence")),
        )

    regular_wide = sorted(
        (
            ticket
            for ticket in tickets
            if str(ticket.get("bet_type", "")) == "wide" and not _is_coverage_ticket(ticket)
        ),
        key=calibrated_rank,
        reverse=True,
    )
    regular_exotics = sorted(
        (
            ticket
            for ticket in tickets
            if str(ticket.get("bet_type", "")) in {"umaren", "umatan", "sanrenpuku", "sanrentan"}
            and not _is_coverage_ticket(ticket)
        ),
        key=calibrated_rank,
        reverse=True,
    )
    allowed_wide_ids = {
        id(ticket) for ticket in regular_wide[: max(0, max_wide_tickets_per_race)]
    }
    allowed_exotic_ids = {
        id(ticket)
        for ticket in _prioritize_exotic_types(regular_exotics)[: max(0, max_exotic_tickets_per_race)]
    }

    selection_pool: list[dict[str, object]] = []
    for ticket in tickets:
        if _is_coverage_ticket(ticket):
            selection_pool.append(ticket)
            continue
        bet_type = str(ticket.get("bet_type", ""))
        if bet_type == "wide" and id(ticket) not in allowed_wide_ids:
            continue
        if bet_type in {"umaren", "umatan", "sanrenpuku", "sanrentan"} and id(ticket) not in allowed_exotic_ids:
            continue
        selection_pool.append(ticket)
    return selection_pool


def _select_optimized_tickets(
    tickets: list[dict[str, object]],
    *,
    per_race_limit: int,
    prefer_wide: bool,
    force_win_standout: bool,
    min_portfolio_ev: float,
    required_horse_number: str = "",
) -> list[dict[str, object]]:
    ranked = _rank_ticket_pool(_dedupe_ticket_combos(tickets), prefer_wide=prefer_wide)
    value_ranked = [ticket for ticket in ranked if not _is_coverage_ticket(ticket)]
    coverage_ranked = [ticket for ticket in ranked if _is_coverage_ticket(ticket)]
    priority_coverage = [
        ticket
        for ticket in coverage_ranked
        if str(ticket.get("coverage_reason", ""))
        in {
            "marked_core_pair_real_odds",
            "marked_core_trio_real_odds",
            "top_model_pair_real_odds",
            "marked_top5_pair_real_odds",
            "marked_top5_trio_real_odds",
            "win_ev_longshot_place_translation",
            "win_ev_longshot_wide_translation",
        }
    ]
    by_type: dict[str, list[dict[str, object]]] = defaultdict(list)
    for ticket in value_ranked:
        by_type[str(ticket.get("bet_type", ""))].append(ticket)

    type_order = list(_selection_type_order(prefer_wide=prefer_wide))
    selected: list[dict[str, object]] = []
    selected_keys: set[tuple[str, str]] = set()

    if required_horse_number:
        required_candidates = [
            ticket
            for ticket in ranked
            if required_horse_number in _ticket_horse_numbers(ticket)
        ]
        if not required_candidates:
            return []
        required_candidates.sort(
            key=lambda ticket: (
                _to_float(ticket.get("hit_prob") or ticket.get("win_prob")),
                _to_float(ticket.get("ev_current") or ticket.get("ev")),
                _to_float(ticket.get("confidence")),
            ),
            reverse=True,
        )
        _append_ticket_if_new(
            selected,
            selected_keys,
            required_candidates[0],
            per_race_limit,
        )

    if force_win_standout and not prefer_wide and by_type["win"]:
        _append_ticket_if_new(selected, selected_keys, by_type["win"][0], per_race_limit)

    for bet_type in type_order:
        if len(selected) >= per_race_limit:
            break
        if not by_type[bet_type]:
            continue
        _append_ticket_if_new(selected, selected_keys, by_type[bet_type][0], per_race_limit)

    for ticket in priority_coverage:
        if len(selected) >= per_race_limit:
            break
        if not _can_add_coverage_ticket(selected, ticket, min_portfolio_ev=min_portfolio_ev):
            continue
        _append_ticket_if_new(selected, selected_keys, ticket, per_race_limit)

    by_ev = sorted(
        value_ranked,
        key=lambda ticket: (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob") or ticket.get("win_prob")),
            _to_float(ticket.get("confidence")),
        ),
        reverse=True,
    )
    for ticket in by_ev:
        if len(selected) >= per_race_limit:
            break
        _append_ticket_if_new(selected, selected_keys, ticket, per_race_limit)

    for ticket in coverage_ranked:
        if len(selected) >= per_race_limit:
            break
        if not _can_add_coverage_ticket(selected, ticket, min_portfolio_ev=min_portfolio_ev):
            continue
        _append_ticket_if_new(selected, selected_keys, ticket, per_race_limit)

    return selected[:per_race_limit]


def _append_ticket_if_new(
    selected: list[dict[str, object]],
    selected_keys: set[tuple[str, str]],
    ticket: dict[str, object],
    per_race_limit: int,
) -> None:
    if len(selected) >= per_race_limit:
        return
    key = (str(ticket.get("bet_type", "")), str(ticket.get("horse_number", "")))
    if key in selected_keys:
        return
    if _would_exceed_horse_dependency(selected, ticket, per_race_limit=per_race_limit):
        return
    selected.append(ticket)
    selected_keys.add(key)


def _calibrate_ticket_probabilities(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    """Conservatively calibrate each ticket type against its live market odds."""
    calibrated: list[dict[str, object]] = []
    for original in tickets:
        ticket = dict(original)
        bet_type = str(ticket.get("bet_type", ""))
        current_odds = _to_float(ticket.get("win_odds") or ticket.get("predicted_odds"), 0.0)
        predicted_odds = _to_float(ticket.get("predicted_odds") or ticket.get("win_odds"), 0.0)
        raw_prob = _to_float(ticket.get("hit_prob") or ticket.get("win_prob"), 0.0)
        if current_odds <= 0 or raw_prob <= 0:
            calibrated.append(ticket)
            continue
        shrink = _ticket_market_shrink(ticket, bet_type=bet_type, odds=current_odds)
        market_prob = min(1.0, 1.0 / current_odds)
        calibrated_prob = ((1.0 - shrink) * raw_prob) + (shrink * market_prob)
        calibrated_ev = calibrated_prob * current_odds
        ticket["raw_hit_prob"] = _fmt(raw_prob)
        ticket["hit_prob"] = _fmt(calibrated_prob)
        if bet_type == "win":
            ticket["win_prob"] = _fmt(calibrated_prob)
        ticket["market_prob"] = _fmt(market_prob)
        ticket["bet_type_market_shrink"] = _fmt(shrink)
        ticket["ev"] = _fmt(calibrated_ev)
        ticket["ev_current"] = _fmt(calibrated_ev)
        ticket["ev_predicted"] = _fmt(calibrated_prob * predicted_odds)
        calibrated.append(ticket)
    return calibrated


def _ticket_market_shrink(ticket: dict[str, object], *, bet_type: str, odds: float) -> float:
    """Continuously increase market shrink for thin, low-evidence win bets."""
    base_shrink = BET_TYPE_MARKET_SHRINK.get(bet_type, 0.65)
    if bet_type != "win" or odds <= 15.0:
        return base_shrink

    consistency = _clamp(_to_float(ticket.get("consistency"), 0.5), minimum=0.0, maximum=1.0)
    history = _clamp(_to_float(ticket.get("history_count"), 0.0) / 5.0, minimum=0.0, maximum=1.0)
    evidence = (0.65 * consistency) + (0.35 * history)
    odds_risk = _clamp(
        math.log(odds / 15.0) / math.log(200.0 / 15.0),
        minimum=0.0,
        maximum=1.0,
    )
    longshot_target = 0.97 - (0.03 * evidence)
    return _clamp(
        base_shrink + ((longshot_target - base_shrink) * odds_risk),
        minimum=base_shrink,
        maximum=0.97,
    )


def _would_exceed_horse_dependency(
    selected: list[dict[str, object]],
    candidate: dict[str, object],
    *,
    per_race_limit: int,
) -> bool:
    cap = max(2, math.ceil(per_race_limit * 0.50))
    counts: dict[str, int] = defaultdict(int)
    for ticket in selected + [candidate]:
        for horse_number in _ticket_horse_numbers(ticket):
            counts[horse_number] += 1
    return any(count > cap for count in counts.values())


def _ticket_horse_numbers(ticket: dict[str, object]) -> set[str]:
    explicit = {str(value) for value in list(ticket.get("horse_numbers") or []) if str(value).strip()}
    if explicit:
        return explicit
    value = str(ticket.get("horse_number", "")).strip()
    normalized = value.replace(">", "-").replace("→", "-")
    return {part for part in normalized.split("-") if part.isdigit()}


def _portfolio_horse_numbers(tickets: list[dict[str, object]]) -> set[str]:
    numbers: set[str] = set()
    for ticket in tickets:
        numbers.update(_ticket_horse_numbers(ticket))
    return numbers


def _top_win_probability_horse_number(rows: list[dict[str, object]]) -> str:
    if not rows:
        return ""
    leader = max(rows, key=lambda row: _to_float(row.get("win_prob")))
    return str(leader.get("horse_number", "")).strip()


def _top_win_probability_horse_numbers(
    rows: list[dict[str, object]],
    *,
    limit: int,
) -> set[str]:
    ranked = sorted(
        rows,
        key=lambda row: _to_float(row.get("win_prob")),
        reverse=True,
    )
    return {
        str(row.get("horse_number", "")).strip()
        for row in ranked[: max(0, limit)]
        if str(row.get("horse_number", "")).strip()
    }


def _max_horse_stake_dependency_ratio(
    tickets: list[dict[str, object]],
    *,
    exempt_horse_numbers: set[str] | None = None,
) -> float:
    """Return the largest amount-weighted cross-ticket dependency on one horse.

    A single ticket necessarily depends 100% on all of its legs, so this portfolio
    diversification constraint is only meaningful when multiple tickets exist.
    """
    if len(tickets) <= 1:
        return 0.0
    total_stake = sum(int(_to_float(ticket.get("stake"), 0.0)) for ticket in tickets)
    if total_stake <= 0:
        return 0.0
    stakes: dict[str, int] = defaultdict(int)
    exempt = exempt_horse_numbers or set()
    for ticket in tickets:
        stake = int(_to_float(ticket.get("stake"), 0.0))
        for horse_number in _ticket_horse_numbers(ticket):
            if horse_number in exempt:
                continue
            stakes[horse_number] += stake
    return max(stakes.values(), default=0) / total_stake


def _ticket_selection_key(ticket: dict[str, object]) -> tuple[str, str]:
    return (
        str(ticket.get("bet_type", "")),
        str(ticket.get("horse_number", "")),
    )


def _annotate_candidate_selection(
    candidates: list[dict[str, object]],
    *,
    selected_tickets: list[dict[str, object]],
    eligible_tickets: list[dict[str, object]],
    selection_pool: list[dict[str, object]],
    portfolio_failure_reason: str = "",
) -> list[dict[str, object]]:
    selected_keys = {_ticket_selection_key(ticket) for ticket in selected_tickets}
    eligible_keys = {_ticket_selection_key(ticket) for ticket in eligible_tickets}
    selection_keys = {_ticket_selection_key(ticket) for ticket in selection_pool}
    annotated: list[dict[str, object]] = []
    for candidate in candidates:
        out = dict(candidate)
        key = _ticket_selection_key(candidate)
        selected = key in selected_keys
        out["selected"] = selected
        out["selection_reason"] = "selected_portfolio" if selected else ""
        if selected:
            out["non_selection_reason"] = ""
        elif key not in eligible_keys:
            out["non_selection_reason"] = "below_minimum_ev"
        elif portfolio_failure_reason:
            out["non_selection_reason"] = portfolio_failure_reason
        elif key not in selection_keys:
            out["non_selection_reason"] = "bet_type_limit"
        else:
            out["non_selection_reason"] = "portfolio_optimization"
        annotated.append(out)
    return annotated


def _is_coverage_ticket(ticket: dict[str, object]) -> bool:
    return str(ticket.get("ticket_role", "")) == "coverage"


def _is_model_pair_coverage_ticket(ticket: dict[str, object]) -> bool:
    return str(ticket.get("coverage_reason", "")) in {
        "marked_core_pair_real_odds",
        "marked_core_trio_real_odds",
        "top_model_pair_real_odds",
        "marked_top5_pair_real_odds",
        "marked_top5_trio_real_odds",
        "win_ev_longshot_place_translation",
        "win_ev_longshot_wide_translation",
    }


def _can_add_coverage_ticket(
    selected: list[dict[str, object]],
    ticket: dict[str, object],
    *,
    min_portfolio_ev: float,
) -> bool:
    portfolio = selected + [ticket]
    if _portfolio_ev(portfolio) < min_portfolio_ev:
        return False
    if str(ticket.get("coverage_reason", "")) in {
        "marked_core_pair_real_odds",
        "marked_core_trio_real_odds",
        "top_model_pair_real_odds",
        "marked_top5_pair_real_odds",
        "marked_top5_trio_real_odds",
        "win_ev_longshot_place_translation",
        "win_ev_longshot_wide_translation",
    }:
        return True
    return _portfolio_no_gami(portfolio)


def _prune_gami_tickets(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    out = [dict(ticket) for ticket in tickets if int(_to_float(ticket.get("stake"), 0.0)) > 0]
    while out and not _portfolio_no_gami(out):
        total_stake = _portfolio_total_stake(out)
        removable = [
            ticket
            for ticket in out
            if _ticket_return_if_hit(ticket) < total_stake and not _is_model_pair_coverage_ticket(ticket)
        ]
        if not removable:
            break
        drop = min(
            removable,
            key=lambda ticket: (
                _to_float(ticket.get("ev_current") or ticket.get("ev")),
                _ticket_return_if_hit(ticket) / max(total_stake, 1),
                _to_float(ticket.get("hit_prob") or ticket.get("win_prob")),
            ),
        )
        out.remove(drop)
    return out


def _annotate_portfolio_tickets(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    if not tickets:
        return []
    total_stake = _portfolio_total_stake(tickets)
    portfolio_ev = _portfolio_ev(tickets)
    expected_return = _portfolio_expected_return(tickets)
    no_gami = _portfolio_no_gami(tickets)
    total_points = _portfolio_total_points(tickets)
    annotated: list[dict[str, object]] = []
    for ticket in tickets:
        out = dict(ticket)
        gross_return = _ticket_return_if_hit(ticket)
        out["portfolio_total_stake"] = total_stake
        out["portfolio_total_points"] = total_points
        out["portfolio_ev"] = _fmt(portfolio_ev)
        out["portfolio_expected_return"] = int(expected_return)
        out["portfolio_expected_profit"] = int(expected_return - total_stake)
        out["portfolio_no_gami"] = no_gami
        out["return_if_hit"] = gross_return
        if _is_formation_ticket(out):
            out["return_if_hit_min"] = gross_return
            out["return_if_hit_max"] = _ticket_max_return_if_hit(out)
        out["net_return_if_hit"] = gross_return - total_stake
        annotated.append(out)
    return annotated


def _selection_type_order(*, prefer_wide: bool) -> tuple[str, ...]:
    if prefer_wide:
        return ("wide", "place", "wakuren", "win", "umaren", "umatan", "sanrenpuku", "sanrentan")
    return ("win", "place", "wide", "wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan")


def _ticket_type_rank(bet_type: str, *, prefer_wide: bool) -> int:
    order = _selection_type_order(prefer_wide=prefer_wide)
    try:
        return order.index(bet_type)
    except ValueError:
        return len(order)


def _candidate_counts_by_type(tickets: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for ticket in tickets:
        counts[str(ticket.get("bet_type", ""))] += 1
    return dict(counts)


def _labels_for_type(tickets: list[dict[str, object]], bet_type: str) -> list[str]:
    return [
        _ticket_combo_label(ticket)
        for ticket in tickets
        if str(ticket.get("bet_type", "")) == bet_type and _ticket_combo_label(ticket)
    ]


def _live_or_current_win_odds(
    row: dict[str, object],
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> float:
    live = _lookup_live_odds(live_odds, "win", [str(row.get("horse_number", ""))])
    return _live_odds_value(live) or _to_float(row.get("current_odds"))


def _win_candidate_ev(
    row: dict[str, object],
    live_odds: dict[tuple[str, str], dict[str, object]],
) -> float:
    odds = _live_or_current_win_odds(row, live_odds)
    return _to_float(row.get("win_prob")) * odds if odds > 0 else _to_float(row.get("ev"))


def _is_win_ev_translation_row(row: dict[str, object]) -> bool:
    odds = _to_float(row.get("current_odds"))
    win_prob = _to_float(row.get("win_prob"))
    ev = _to_float(row.get("ev_current") or row.get("ev"))
    if odds < 10.0 or win_prob < 0.035:
        return False
    if ev >= 0.98:
        return True
    band = str(row.get("probability_band") or _probability_band_from_row(row))
    return ev >= 0.92 and band in {"outsider", "longshot"}


def _win_ev_translation_score(row: dict[str, object]) -> float:
    ev = _to_float(row.get("ev_current") or row.get("ev"))
    win_prob = _to_float(row.get("win_prob"))
    odds = _to_float(row.get("current_odds"))
    place_edge = _to_float(row.get("place_edge"))
    return ev + (0.30 * win_prob) + (0.04 * min(odds / 10.0, 4.0)) + max(0.0, place_edge)


def _is_top_two_model_pair(ticket: dict[str, object], core: list[dict[str, object]]) -> bool:
    top_two = {str(row.get("horse_number", "")) for row in core[:2]}
    ticket_numbers = {str(number) for number in list(ticket.get("horse_numbers") or [])}
    return ticket_numbers == top_two


def _frame_combo_hit_prob(
    left_rows: list[dict[str, object]],
    right_rows: list[dict[str, object]],
    *,
    same_frame: bool,
    key: str,
) -> float:
    total = 0.0
    if same_frame:
        for order in permutations(left_rows, 2):
            total += _ordered_finish_prob(list(order), key=key)
        return total

    for left in left_rows:
        for right in right_rows:
            total += _ordered_finish_prob([left, right], key=key)
            total += _ordered_finish_prob([right, left], key=key)
    return total


def _frame_quality_adjustment(
    left_rows: list[dict[str, object]],
    right_rows: list[dict[str, object]],
    *,
    same_frame: bool,
) -> float:
    if same_frame:
        return _single_frame_quality(left_rows) ** 2
    return _single_frame_quality(left_rows) * _single_frame_quality(right_rows)


def _single_frame_quality(rows: list[dict[str, object]]) -> float:
    if len(rows) <= 1:
        return 1.0
    probs = sorted((_to_float(row.get("win_prob")) for row in rows), reverse=True)
    top = probs[0] if probs else 0.0
    if top <= 0:
        return 0.80
    second = probs[1] if len(probs) > 1 else 0.0
    depth_ratio = second / top
    return _clamp(0.82 + (0.18 * depth_ratio), minimum=0.82, maximum=1.0)


def _frame_horse_summaries(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "horse_id": str(row.get("horse_id", "")),
            "horse_name": str(row.get("horse_name", "")),
            "horse_number": str(row.get("horse_number", "")),
            "win_prob": str(row.get("win_prob", "")),
        }
        for row in rows
    ]


def _resolve_frame_number(row: dict[str, object], *, field_size: int) -> str:
    frame_number = str(row.get("frame_number", "")).strip()
    if frame_number:
        return frame_number

    horse_number = int(_to_float(row.get("horse_number"), 0.0))
    if horse_number <= 0:
        return ""
    if field_size <= 8:
        return str(min(horse_number, 8))

    base = field_size // 8
    remainder = field_size % 8
    slots = [base for _ in range(8)]
    for idx in range(8 - remainder, 8):
        if 0 <= idx < 8:
            slots[idx] += 1

    current = 1
    for frame_idx, slot_count in enumerate(slots, start=1):
        if current <= horse_number < current + max(slot_count, 0):
            return str(frame_idx)
        current += max(slot_count, 0)
    return str(min(8, horse_number))


def _frame_sort_key(value: str) -> tuple[int, str]:
    number = int(_to_float(value, 999.0))
    return (number, value)


def _has_win_standout(rows: list[dict[str, object]]) -> bool:
    if not rows:
        return False
    leader = max(rows, key=lambda row: _to_float(row.get("win_prob")))
    return (
        _to_float(leader.get("win_prob")) >= 0.20
        and _to_float(leader.get("ev")) >= MIN_ACTIONABLE_WIN_EV
    )


def _optimize_portfolio_stakes(
    tickets: list[dict[str, object]],
    *,
    bankroll_per_race: int,
    min_portfolio_ev: float,
    max_horse_stake_dependency_ratio: float = 0.60,
    stake_dependency_exempt_horse_numbers: set[str] | None = None,
) -> list[dict[str, object]]:
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
            if (
                _max_horse_stake_dependency_ratio(trial)
                if not stake_dependency_exempt_horse_numbers
                else _max_horse_stake_dependency_ratio(
                    trial,
                    exempt_horse_numbers=stake_dependency_exempt_horse_numbers,
                )
            ) > max_horse_stake_dependency_ratio:
                continue

            score = _stake_allocation_score(ticket)
            if score > best_score:
                best_idx = idx
                best_score = score
                best_trial = trial

        if best_idx < 0 or best_trial is None:
            break
        allocated = best_trial

    return allocated


def _ticket_max_portfolio_stake(ticket: dict[str, object], *, bankroll_per_race: int) -> int:
    bet_type = str(ticket.get("bet_type", ""))
    if _is_formation_ticket(ticket):
        share = 0.70
    elif bet_type in {"place", "wide"}:
        share = 0.45
    elif bet_type == "win":
        share = 0.35
    elif bet_type in {"wakuren", "umaren"}:
        share = 0.30
    elif bet_type in {"umatan", "sanrenpuku"}:
        share = 0.24
    elif bet_type == "sanrentan":
        share = 0.20
    else:
        share = 0.20

    unit = _ticket_stake_unit(ticket)
    max_stake = int((bankroll_per_race * share) / unit) * unit
    return max(unit, max_stake)


def _stake_allocation_score(ticket: dict[str, object]) -> float:
    ev = _to_float(ticket.get("ev_current") or ticket.get("ev"))
    edge = max(0.0, ev - 1.0)
    if edge <= 0:
        return 0.0
    hit_prob = _to_float(ticket.get("hit_prob") or ticket.get("win_prob"))
    confidence = _clamp(_to_float(ticket.get("confidence"), 1.0), minimum=0.30, maximum=2.50)
    odds = max(1.0, _to_float(ticket.get("win_odds") or ticket.get("predicted_odds"), 1.0))
    stability = 1.0 / (1.0 + abs(_to_float(ticket.get("ev_predicted")) - ev))
    shape_bonus = 1.08 if _is_formation_ticket(ticket) else 1.0
    return edge * math.sqrt(max(hit_prob, 0.001)) * confidence * stability * shape_bonus / math.sqrt(odds)


def _rebalance_race_stakes(
    tickets: list[dict[str, object]],
    *,
    bankroll_per_race: int,
) -> list[dict[str, object]]:
    if not tickets:
        return []

    total = sum(int(_to_float(ticket.get("stake"), 0.0)) for ticket in tickets)
    if total <= bankroll_per_race:
        return tickets

    scaled: list[dict[str, object]] = []
    scale = bankroll_per_race / max(total, 1)
    for ticket in tickets:
        stake = int(_to_float(ticket.get("stake"), 0.0))
        unit = _ticket_stake_unit(ticket)
        adjusted = int((stake * scale) / unit) * unit
        if adjusted <= 0:
            continue
        scaled.append(_with_adjusted_stake(ticket, adjusted))

    if not scaled:
        best = dict(max(tickets, key=lambda ticket: _to_float(ticket.get("ev_current") or ticket.get("ev"))))
        best = _with_adjusted_stake(best, min(_ticket_stake_unit(best), bankroll_per_race))
        return [best] if int(_to_float(best.get("stake"), 0.0)) > 0 else []
    return scaled


def _kelly_stake(
    *,
    probability: float,
    odds: float,
    bankroll_per_race: int,
    kelly_fraction: float,
    min_ev: float,
    max_fraction: float,
) -> int:
    if probability <= 0 or odds <= 1.0:
        return 0
    ev = probability * odds
    if ev <= 1.0:
        return 0

    full_kelly = ((odds * probability) - 1.0) / max(odds - 1.0, 1e-6)
    recommended_fraction = max(0.0, min(max_fraction, full_kelly * kelly_fraction))
    stake = int((bankroll_per_race * recommended_fraction) / 100) * 100
    if stake < 100:
        stake = 100 if ev >= min_ev else 0
    return stake


def _probability_band_from_row(row: dict[str, object]) -> str:
    odds = _to_float(row.get("current_odds"))
    popularity = _to_float(row.get("current_popularity") or row.get("popularity_latest"))
    if (popularity > 0 and popularity <= 3) or (odds > 0 and odds <= 6.0):
        return "favorite"
    if (popularity > 0 and popularity <= 8) or (odds > 0 and odds <= 15.0):
        return "contender"
    if (popularity > 0 and popularity <= 12) or (odds > 0 and odds <= 30.0):
        return "outsider"
    return "longshot"


def _clamp(value: float, *, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
