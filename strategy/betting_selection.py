from __future__ import annotations

from strategy.portfolio import is_formation_ticket as _is_formation_ticket
from strategy.portfolio import is_wide_box_ticket as _is_wide_box_ticket
from strategy.portfolio import portfolio_ev as _portfolio_ev
from strategy.portfolio import portfolio_expected_return as _portfolio_expected_return
from strategy.portfolio import portfolio_no_gami as _portfolio_no_gami
from strategy.portfolio import portfolio_robust_ev as _portfolio_robust_ev
from strategy.portfolio import portfolio_robust_expected_return as _portfolio_robust_expected_return
from strategy.portfolio import portfolio_summary as _portfolio_summary
from strategy.portfolio import portfolio_total_points as _portfolio_total_points
from strategy.portfolio import portfolio_total_stake as _portfolio_total_stake
from strategy.portfolio import ticket_max_return_if_hit as _ticket_max_return_if_hit
from strategy.portfolio import ticket_return_if_hit as _ticket_return_if_hit
from strategy.portfolio import ticket_stake_unit as _ticket_stake_unit
from strategy.portfolio import with_adjusted_stake as _with_adjusted_stake
from analysis.candidate_ev import canonical_combination
from collections import defaultdict
import math
from strategy.betting_common import BET_TYPE_MARKET_SHRINK, MIN_ACTIONABLE_WIN_EV, _clamp, _combo_name, _coverage_priority, _fmt, _horse_summary, _is_coverage_ticket, _is_model_pair_coverage_ticket, _prioritize_exotic_types, _selection_type_order, _ticket_horse_numbers, _ticket_market_shrink, _ticket_selection_key, _to_float


def _probability_lineage_failure_plan(
    validation: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "core": [],
        "partner": [],
        "long": [],
        "front_value_candidates": [],
        "tickets": [],
        "races": [],
        "bet_types_considered": list(BET_TYPE_MARKET_SHRINK),
        "candidate_counts": {},
        "optimization_mode": "canonical_probability_robust_odds",
        "selection_status": "no_bet",
        "selection_reason": "probability_lineage_invalid",
        "decision": "NO_GO",
        "decision_code": "NO_GO_PROBABILITY_LINEAGE_INVALID",
        "probability_lineage": {
            "status": "NG",
            "validation": dict(validation or {}),
        },
        "portfolio_summary": _portfolio_summary([]),
    }

def _index_odds_history(
    odds_history: list[dict[str, object]] | list[dict[str, str]],
) -> dict[tuple[str, str, str], list[dict[str, object] | dict[str, str]]]:
    indexed: dict[
        tuple[str, str, str],
        list[dict[str, object] | dict[str, str]],
    ] = defaultdict(list)
    for row in odds_history:
        race_id = str(row.get("race_id", "")).strip()
        bet_type = str(row.get("bet_type", "")).strip()
        if not race_id or bet_type not in BET_TYPE_MARKET_SHRINK:
            continue
        try:
            combination = canonical_combination(bet_type, row.get("combination", ""))
        except (TypeError, ValueError):
            continue
        indexed[(race_id, bet_type, combination)].append(row)
    return dict(indexed)

def _ticket_decision_ev(ticket: dict[str, object]) -> float:
    """Return the conservative EV used for selection without relabeling live EV."""
    return _to_float(ticket.get("robust_ev") or ticket.get("ev_current") or ticket.get("ev"))

def _ticket_decision_probability(ticket: dict[str, object]) -> float:
    """Return the conservative probability used for selection when available."""
    return _to_float(
        ticket.get("robust_hit_prob") or ticket.get("hit_prob") or ticket.get("win_prob")
    )

def _ticket_decision_odds(ticket: dict[str, object]) -> float:
    """Return conservative scenario odds for allocation, else the current market odds."""
    return _to_float(
        ticket.get("robust_odds") or ticket.get("win_odds") or ticket.get("predicted_odds")
    )

def _rank_ticket_pool(tickets: list[dict[str, object]], *, prefer_wide: bool) -> list[dict[str, object]]:
    return sorted(
        tickets,
        key=lambda ticket: (
            _ticket_type_rank(str(ticket.get("bet_type", "")), prefer_wide=prefer_wide),
            -_ticket_decision_ev(ticket),
            -_ticket_decision_probability(ticket),
            -_to_float(ticket.get("confidence")),
        ),
    )

def _ticket_type_rank(bet_type: str, *, prefer_wide: bool) -> int:
    order = _selection_type_order(prefer_wide=prefer_wide)
    try:
        return order.index(bet_type)
    except ValueError:
        return len(order)

def _limit_eligible_selection_pool(
    tickets: list[dict[str, object]],
    *,
    max_wide_tickets_per_race: int,
    max_exotic_tickets_per_race: int,
) -> list[dict[str, object]]:
    """Apply type caps only after calibration so rejected tickets can be refilled."""
    def calibrated_rank(ticket: dict[str, object]) -> tuple[float, float, float]:
        return (
            _ticket_decision_ev(ticket),
            _ticket_decision_probability(ticket),
            _to_float(ticket.get("confidence")),
        )

    regular_wide = sorted(
        (
            ticket
            for ticket in tickets
            if str(ticket.get("bet_type", "")) == "wide"
            and not _is_coverage_ticket(ticket)
            and not _is_wide_box_ticket(ticket)
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
        if _is_wide_box_ticket(ticket):
            selection_pool.append(ticket)
            continue
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
    safe_wide_boxes = [
        ticket
        for ticket in ranked
        if _is_wide_box_ticket(ticket)
        and bool(ticket.get("strict_no_gami_current"))
        and bool(ticket.get("strict_no_gami_robust"))
        and (
            not required_horse_number
            or required_horse_number in _ticket_horse_numbers(ticket)
        )
    ]
    if safe_wide_boxes:
        return [
            max(
                safe_wide_boxes,
                key=lambda ticket: (
                    _ticket_decision_ev(ticket),
                    _ticket_decision_probability(ticket),
                ),
            )
        ]
    ranked = [ticket for ticket in ranked if not _is_wide_box_ticket(ticket)]
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
            "top3_rank_coverage",
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
                _ticket_decision_probability(ticket),
                _ticket_decision_ev(ticket),
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
            _ticket_decision_ev(ticket),
            _ticket_decision_probability(ticket),
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

def _candidate_eligibility_checks(
    ticket: dict[str, object],
    *,
    minimum_ev: float,
    minimum_hit_probability: float,
) -> list[dict[str, object]]:
    """Record every canonical eligibility gate, using the exact selection values."""
    checks = [
        {
            "reason": reason,
            "metric": metric,
            "value": value,
            "minimum": minimum,
            "passed": value >= minimum,
        }
        for reason, metric, value, minimum in (
            ("below_minimum_ev", "robust_ev", _to_float(ticket.get("robust_ev")), minimum_ev),
            (
                "below_minimum_hit_probability",
                "decision_hit_prob",
                _ticket_decision_probability(ticket),
                minimum_hit_probability,
            ),
            ("below_minimum_stake", "stake", int(_to_float(ticket.get("stake"))), 100),
        )
    ]
    return checks

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
            reasons = []
        elif key not in eligible_keys:
            reasons = [
                check["reason"]
                for check in candidate.get("eligibility_checks", [])
                if not check["passed"]
            ] or ["below_minimum_ev"]
        elif portfolio_failure_reason:
            reasons = [portfolio_failure_reason]
        elif key not in selection_keys:
            reasons = ["bet_type_limit"]
        else:
            reasons = ["portfolio_optimization"]
        out["non_selection_reason"] = reasons[0] if reasons else ""
        out["non_selection_reasons"] = reasons
        annotated.append(out)
    return annotated

def _is_top3_rank_coverage_ticket(ticket: dict[str, object]) -> bool:
    return (
        _is_coverage_ticket(ticket)
        and str(ticket.get("coverage_reason", "")) == "top3_rank_coverage"
    )

def _can_add_coverage_ticket(
    selected: list[dict[str, object]],
    ticket: dict[str, object],
    *,
    min_portfolio_ev: float,
) -> bool:
    portfolio = selected + [ticket]
    if _is_top3_rank_coverage_ticket(ticket):
        return _portfolio_no_gami(portfolio)
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
        "top3_rank_coverage",
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
                _ticket_decision_ev(ticket),
                _ticket_return_if_hit(ticket) / max(total_stake, 1),
                _ticket_decision_probability(ticket),
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
    robust_portfolio_ev = _portfolio_robust_ev(tickets)
    robust_expected_return = _portfolio_robust_expected_return(tickets)
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
        out["robust_portfolio_ev"] = _fmt(robust_portfolio_ev)
        out["robust_portfolio_expected_return"] = int(robust_expected_return)
        out["robust_portfolio_expected_profit"] = int(
            robust_expected_return - total_stake
        )
        out["portfolio_no_gami"] = no_gami
        out["return_if_hit"] = gross_return
        if _is_formation_ticket(out) or _is_wide_box_ticket(out):
            out["return_if_hit_min"] = gross_return
            out["return_if_hit_max"] = _ticket_max_return_if_hit(out)
        out["net_return_if_hit"] = gross_return - total_stake
        annotated.append(out)
    return annotated

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

    # Preserve every explicitly admitted longshot lane before assigning the
    # mutually-exclusive core and partner classifications.
    longshots = unique(longshots)
    core = unique(core)
    partner = unique(partner)
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


def _ticket_max_portfolio_stake(ticket: dict[str, object], *, bankroll_per_race: int) -> int:
    bet_type = str(ticket.get("bet_type", ""))
    if _is_formation_ticket(ticket) or _is_wide_box_ticket(ticket):
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
    ev = _ticket_decision_ev(ticket)
    edge = max(0.0, ev - 1.0)
    if edge <= 0:
        return 0.0
    hit_prob = _ticket_decision_probability(ticket)
    confidence = _clamp(_to_float(ticket.get("confidence"), 1.0), minimum=0.30, maximum=2.50)
    odds = max(1.0, _ticket_decision_odds(ticket))
    stability = 1.0 / (1.0 + abs(_to_float(ticket.get("ev_predicted")) - ev))
    shape_bonus = 1.08 if _is_formation_ticket(ticket) or _is_wide_box_ticket(ticket) else 1.0
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
        best = dict(max(tickets, key=_ticket_decision_ev))
        best = _with_adjusted_stake(best, min(_ticket_stake_unit(best), bankroll_per_race))
        return [best] if int(_to_float(best.get("stake"), 0.0)) > 0 else []
    return scaled

def _has_win_standout(rows: list[dict[str, object]]) -> bool:
    if not rows:
        return False
    leader = max(rows, key=lambda row: _to_float(row.get("win_prob")))
    return (
        _to_float(leader.get("win_prob")) >= 0.20
        and _to_float(leader.get("ev")) >= MIN_ACTIONABLE_WIN_EV
    )
