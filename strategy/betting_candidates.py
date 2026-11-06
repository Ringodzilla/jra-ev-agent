from __future__ import annotations

from itertools import combinations
from collections import defaultdict
import math
from itertools import permutations
from strategy.betting_common import MIN_ACTIONABLE_WIN_EV, _axis_values, _dedupe_rows_by_horse_number, _fmt, _formation_axis_rows, _frame_sort_key, _horse_summary, _is_top_two_model_pair, _prioritize_exotic_types, _probability_band_from_row, _resolve_frame_number, _to_float, _with_long_reason
from strategy.betting_estimates import _normalize_to_target
from strategy.betting_selection import _dedupe_ticket_combos
from strategy.betting_tickets import _build_exotic_ticket, _build_place_ticket, _build_wakuren_ticket, _build_wide_ticket



def _formation_points(*args, **kwargs):
    # Resolved through strategy.betting at call time so that tests patching
    # strategy.betting._formation_points keep working after the module split.
    from strategy import betting

    return betting._formation_points(*args, **kwargs)

def _live_odds_value(*args, **kwargs):
    # Resolved through strategy.betting at call time so that tests patching
    # strategy.betting._live_odds_value keep working after the module split.
    from strategy import betting

    return betting._live_odds_value(*args, **kwargs)

def _lookup_live_odds(*args, **kwargs):
    # Resolved through strategy.betting at call time so that tests patching
    # strategy.betting._lookup_live_odds keep working after the module split.
    from strategy import betting

    return betting._lookup_live_odds(*args, **kwargs)

def _promote_top3_rank_coverage_candidates(
    candidates: list[dict[str, object]],
    race_rows: list[dict[str, object]],
    *,
    enabled: bool,
    budget_yen: int,
    min_robust_ev: float,
    min_top3_probability: float,
    min_leader_probability: float,
    min_third_vs_fourth_ratio: float,
) -> dict[str, object]:
    """Fund a tiny top-three order cover when the probability ranking is decisive."""
    ranked = sorted(
        race_rows,
        key=lambda row: _to_float(row.get("win_prob")),
        reverse=True,
    )
    top3 = ranked[:3]
    probabilities = [_to_float(row.get("win_prob")) for row in top3]
    top3_probability = sum(probabilities)
    fourth_probability = _to_float(ranked[3].get("win_prob")) if len(ranked) > 3 else 0.0
    third_vs_fourth_ratio = (
        probabilities[2] / fourth_probability
        if len(probabilities) == 3 and fourth_probability > 0
        else float("inf")
    )
    criteria = {
        "enabled": enabled,
        "three_ranked_horses": len(top3) == 3,
        "budget_available": budget_yen >= 100,
        "top3_probability": _fmt(top3_probability),
        "min_top3_probability": _fmt(min_top3_probability),
        "top3_probability_passed": top3_probability >= min_top3_probability,
        "leader_probability": _fmt(probabilities[0] if probabilities else 0.0),
        "min_leader_probability": _fmt(min_leader_probability),
        "leader_probability_passed": bool(probabilities)
        and probabilities[0] >= min_leader_probability,
        "third_vs_fourth_ratio": _fmt(third_vs_fourth_ratio),
        "min_third_vs_fourth_ratio": _fmt(min_third_vs_fourth_ratio),
        "third_vs_fourth_ratio_passed": len(top3) == 3
        and third_vs_fourth_ratio >= min_third_vs_fourth_ratio,
    }
    triggered = all(
        bool(criteria[key])
        for key in (
            "enabled",
            "three_ranked_horses",
            "budget_available",
            "top3_probability_passed",
            "leader_probability_passed",
            "third_vs_fourth_ratio_passed",
        )
    )
    numbers = [str(row.get("horse_number", "")).strip() for row in top3]
    if not triggered:
        return {
            "triggered": False,
            "top3_horse_numbers": numbers,
            "criteria": criteria,
            "candidates": [],
        }

    leader, second, third = numbers
    target_keys = [
        ("sanrenpuku", "-".join(sorted(numbers, key=lambda value: int(_to_float(value))))),
        ("sanrentan", f"{leader}>{second}>{third}"),
        ("sanrentan", f"{leader}>{third}>{second}"),
    ]
    by_key = {
        (str(ticket.get("bet_type", "")), str(ticket.get("horse_number", ""))): ticket
        for ticket in candidates
    }
    promoted: list[dict[str, object]] = []
    remaining = max(0, budget_yen)
    for key in target_keys:
        ticket = by_key.get(key)
        if (
            ticket is not None
            and remaining >= 100
            and str(ticket.get("odds_source", "")) == "jra_live"
            and _to_float(ticket.get("robust_ev")) >= min_robust_ev
        ):
            ticket.update(
                {
                    "stake": 100,
                    "ticket_role": "coverage",
                    "coverage_reason": "top3_rank_coverage",
                    "coverage_budget_yen": budget_yen,
                    "coverage_min_robust_ev": _fmt(min_robust_ev),
                    "coverage_top3_horse_numbers": numbers,
                    "coverage_rank_clarity": dict(criteria),
                }
            )
            promoted.append(ticket)
            remaining -= 100

    return {
        "triggered": bool(promoted),
        "top3_horse_numbers": numbers,
        "criteria": criteria,
        "budget_yen": budget_yen,
        "stake": sum(int(_to_float(ticket.get("stake"))) for ticket in promoted),
        "candidates": [
            {
                "bet_type": str(ticket.get("bet_type", "")),
                "horse_number": str(ticket.get("horse_number", "")),
                "robust_ev": str(ticket.get("robust_ev", "")),
                "stake": int(_to_float(ticket.get("stake"))),
            }
            for ticket in promoted
        ],
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
        if not first_rows or not second_rows or not third_rows:  # pragma: no cover - len(rows) >= 3
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
        if stake > bankroll_per_race:  # pragma: no cover - point_count is capped by bankroll
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
    if len(core) < 3:  # pragma: no cover - rows length was checked before slicing
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
    if len(core) < 3:  # pragma: no cover - rows length was checked before slicing
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
    if len(marked) < 3:  # pragma: no cover - rows length was checked before slicing
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

def _build_race_longshots(
    rows: list[dict[str, object]],
    *,
    min_win_ev: float,
    limit: int,
) -> list[dict[str, object]]:
    if not rows or limit <= 0:
        return []

    front_longs = [
        _with_long_reason(row, "front_value_candidate")
        for row in rows
        if row.get("front_value_candidate") is True
    ]
    value_longs = [
        _with_long_reason(row, "win_ev_threshold")
        for row in rows
        if _to_float(row.get("current_odds")) >= 10.0
        and _to_float(row.get("ev")) >= max(min_win_ev, MIN_ACTIONABLE_WIN_EV)
    ]
    model_longs = _model_score_longshots(rows)
    merged = _dedupe_rows_by_horse_number(front_longs + value_longs + model_longs)
    merged.sort(
        key=lambda row: (
            {
                "front_value_candidate": 2,
                "top_model_score_longshot": 1,
            }.get(str(row.get("long_reason")), 0),
            _to_float(row.get("front_value_score")),
            _to_float(row.get("ev")),
            _to_float(row.get("model_score")),
            _to_float(row.get("win_prob")),
        ),
        reverse=True,
    )
    return [_horse_summary(row) for row in merged[:limit]]

def _front_value_candidates(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    candidates = [row for row in rows if row.get("front_value_candidate") is True]
    candidates.sort(
        key=lambda row: (
            _to_float(row.get("front_value_score")),
            _to_float(row.get("structural_ev_current") or row.get("ev")),
        ),
        reverse=True,
    )
    return [_horse_summary(row) for row in candidates]

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


