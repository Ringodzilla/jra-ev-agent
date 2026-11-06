from __future__ import annotations

from strategy.portfolio import is_wide_box_ticket as _is_wide_box_ticket
from strategy.odds_scenarios import build_odds_scenarios
from itertools import combinations
from strategy.betting_common import MIN_ACTIONABLE_WIN_EV, MIN_WIN_EV, _clamp, _combo_name, _fmt, _frame_horse_summaries, _probability_band_from_row, _ticket_market_shrink, _to_float
from strategy.betting_estimates import _combo_hit_prob, _estimate_exotic_odds, _estimate_market_pair_odds, _estimate_market_pair_prob, _estimate_pair_hit_prob, _estimate_place_odds, _estimate_predicted_combo_odds, _estimate_predicted_pair_odds, _frame_combo_hit_prob, _frame_quality_adjustment, _wide_pace_adjustment



def _kelly_stake(*args, **kwargs):
    # Resolved through strategy.betting at call time so that tests patching
    # strategy.betting._kelly_stake keep working after the module split.
    from strategy import betting

    return betting._kelly_stake(*args, **kwargs)

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

def _canonical_ticket_from_evaluation(
    evaluation: dict[str, object],
    *,
    row_by_number: dict[str, dict[str, object]],
    odds_history: list[dict[str, object]] | list[dict[str, str]],
    bankroll_per_race: int,
    kelly_fraction: float,
    seconds_to_post: float,
    min_ev: float,
) -> dict[str, object]:
    bet_type = str(evaluation.get("bet_type", ""))
    combination = str(evaluation.get("canonical_combination", ""))
    tokens = combination.replace(">", "-").split("-")
    if bet_type == "wakuren":
        frames = tokens
        horse_numbers = sorted(
            {
                number
                for number, row in row_by_number.items()
                if str(row.get("frame_number", "")) in frames
            },
            key=int,
        )
    else:
        frames = []
        horse_numbers = tokens
    horse_names = [
        str(row_by_number.get(number, {}).get("horse_name", number))
        for number in horse_numbers
    ]
    constituent_rows = [row_by_number[number] for number in horse_numbers if number in row_by_number]
    raw_prob = _to_float(evaluation.get("hit_prob"))
    official_odds = _to_float(evaluation.get("official_odds"))
    ticket: dict[str, object] = {
        "race_id": str(evaluation.get("race_id", "")),
        "bet_type": bet_type,
        "horse_number": combination,
        "horse_numbers": horse_numbers,
        "horse_names": horse_names,
        "horse_name": _combo_name(horse_names, bet_type=bet_type),
        "frame_numbers": frames,
        "raw_hit_prob": raw_prob,
        "hit_prob": raw_prob,
        "official_odds_current": official_odds,
        "predicted_odds": official_odds,
        "win_odds": official_odds,
        "odds_source": "jra_live",
        "probability_source": "04_ev_calculator",
        "source_candidate_key": str(evaluation.get("key", "")),
        "source_snapshot_id": str(evaluation.get("snapshot_id", "")),
        "source_captured_at": str(evaluation.get("captured_at", "")),
        "history_count": min(
            (_to_float(row.get("history_count"), 0.0) for row in constituent_rows),
            default=0.0,
        ),
        "consistency": min(
            (_to_float(row.get("consistency"), 0.5) for row in constituent_rows),
            default=0.5,
        ),
        "legs": [
            {
                "horse_number": number,
                "horse_name": str(row_by_number.get(number, {}).get("horse_name", number)),
                "win_prob": str(row_by_number.get(number, {}).get("win_prob", "")),
            }
            for number in horse_numbers
        ],
    }
    shrink = _ticket_market_shrink(ticket, bet_type=bet_type, odds=official_odds)
    ticket["bet_type_market_shrink"] = _fmt(shrink)
    market_prob = min(1.0, 1.0 / official_odds)
    calibrated_prob = ((1.0 - shrink) * raw_prob) + (shrink * market_prob)
    current_hit_prob = _to_float(_fmt(calibrated_prob))
    ticket["snapshot_calibrated_hit_prob"] = _fmt(current_hit_prob)
    ticket["snapshot_ev"] = _fmt(current_hit_prob * official_odds)
    scenario = build_odds_scenarios(
        ticket,
        official_odds,
        odds_history,
        list(row_by_number.values()),
        seconds_to_post,
    )
    robust_odds = _to_float(scenario.get("robust_odds"))
    robust_prob = _to_float(scenario.get("robust_hit_prob"))
    robust_ev = _to_float(scenario.get("robust_ev"))
    current_ev = current_hit_prob * official_odds
    stake = _kelly_stake(
        probability=robust_prob,
        odds=robust_odds,
        bankroll_per_race=bankroll_per_race,
        kelly_fraction=kelly_fraction,
        min_ev=min_ev,
        max_fraction=0.30,
    )
    ticket.update(
        {
            "stake": stake,
            "hit_prob": _fmt(current_hit_prob),
            "calibrated_hit_prob": _fmt(current_hit_prob),
            "market_prob": _fmt(market_prob),
            "robust_odds": _fmt(robust_odds),
            "robust_hit_prob": _fmt(robust_prob),
            "predicted_odds": _fmt(robust_odds),
            "win_odds": _fmt(official_odds),
            "robust_ev": _fmt(robust_ev),
            "ev": _fmt(current_ev),
            "ev_current": _fmt(current_ev),
            "ev_predicted": _fmt(current_hit_prob * robust_odds),
            "confidence": _fmt(raw_prob / max(market_prob, 1e-9)),
            "odds_scenarios": list(scenario.get("scenarios") or []),
            "odds_scenario_audit": dict(scenario.get("audit") or {}),
            "probability_lineage": {
                "source": "04_ev_calculator",
                "candidate_key": str(evaluation.get("key", "")),
                "snapshot_id": str(evaluation.get("snapshot_id", "")),
                "captured_at": str(evaluation.get("captured_at", "")),
                "raw_hit_prob": raw_prob,
                "tolerance": 1e-9,
            },
        }
    )
    alias = {
        "place": "place_prob",
        "wide": "wide_prob",
        "wakuren": "combo_prob",
        "umaren": "combo_prob",
        "umatan": "combo_prob",
        "sanrenpuku": "combo_prob",
        "sanrentan": "combo_prob",
    }.get(bet_type)
    if alias:
        ticket[alias] = _fmt(current_hit_prob)
    return ticket

def _build_top5_wide_box_candidate(
    candidates: list[dict[str, object]],
    race_rows: list[dict[str, object]],
    *,
    bankroll_per_race: int,
) -> dict[str, object] | None:
    """Build one atomic 5-horse wide box from the five highest win probabilities."""
    point_count = 10
    stake_per_point = 100
    total_stake = point_count * stake_per_point
    if bankroll_per_race < total_stake or len(race_rows) < 5:
        return None

    top5 = sorted(
        race_rows,
        key=lambda row: _to_float(row.get("win_prob")),
        reverse=True,
    )[:5]
    rows_by_number = {
        str(row.get("horse_number", "")).strip(): row
        for row in top5
        if str(row.get("horse_number", "")).strip()
    }
    if len(rows_by_number) != 5:
        return None
    numbers = sorted(rows_by_number, key=lambda value: int(_to_float(value)))
    pair_tickets = {
        str(ticket.get("horse_number", "")): ticket
        for ticket in candidates
        if str(ticket.get("bet_type", "")) == "wide"
        and not _is_wide_box_ticket(ticket)
    }
    points: list[dict[str, object]] = []
    for left, right in combinations(numbers, 2):
        combination = f"{left}-{right}"
        source = pair_tickets.get(combination)
        if (
            source is None
            or str(source.get("odds_source", "")) != "jra_live"
            or _to_float(source.get("official_odds_current")) <= 1.0
            or _to_float(source.get("robust_odds")) <= 1.0
        ):
            return None
        probability = _to_float(source.get("raw_hit_prob") or source.get("hit_prob"))
        if probability <= 0.0:
            return None
        points.append(
            {
                "combination": combination,
                "horse_numbers": [left, right],
                "hit_prob": probability,
                "odds": _to_float(source.get("official_odds_current")),
                "robust_odds": _to_float(source.get("robust_odds")),
                "odds_source": "jra_live",
                "source_candidate_key": str(source.get("source_candidate_key", "")),
            }
        )
    triple_probabilities = {
        tuple(combo): _combo_hit_prob(
            [rows_by_number[number] for number in combo],
            key="win_prob",
            bet_type="sanrenpuku",
        )
        for combo in combinations(numbers, 3)
    }
    pair_probability_sum = sum(_to_float(point.get("hit_prob")) for point in points)
    three_hit_probability = sum(triple_probabilities.values())
    any_hit_probability = _clamp(
        pair_probability_sum - (2.0 * three_hit_probability),
        minimum=0.0,
        maximum=1.0,
    )
    if any_hit_probability <= 0.0:
        return None

    current_expected_return = sum(
        stake_per_point * _to_float(point.get("hit_prob")) * _to_float(point.get("odds"))
        for point in points
    )
    robust_expected_return = sum(
        stake_per_point
        * _to_float(point.get("hit_prob"))
        * _to_float(point.get("robust_odds"))
        for point in points
    )
    current_ev = current_expected_return / total_stake
    robust_ev = robust_expected_return / total_stake
    outcome_audit = _wide_box_outcome_audit(
        points,
        race_rows,
        total_stake=total_stake,
        stake_per_point=stake_per_point,
    )
    horse_names = [str(rows_by_number[number].get("horse_name", number)) for number in numbers]
    race_id = str(top5[0].get("race_id", ""))
    return {
        "race_id": race_id,
        "bet_type": "wide",
        "ticket_shape": "wide_box",
        "ticket_role": "portfolio_candidate",
        "horse_number": "-".join(numbers),
        "horse_numbers": numbers,
        "horse_names": horse_names,
        "horse_name": f"ワイドBOX {'-'.join(numbers)}",
        "points": points,
        "point_count": point_count,
        "stake_per_point": stake_per_point,
        "stake": total_stake,
        "raw_hit_prob": any_hit_probability,
        "hit_prob": _fmt(any_hit_probability),
        "robust_hit_prob": _fmt(any_hit_probability),
        "wide_prob": _fmt(any_hit_probability),
        "three_hit_probability": _fmt(three_hit_probability),
        "expected_hit_count": _fmt(pair_probability_sum),
        "current_expected_return": _fmt(current_expected_return),
        "robust_expected_return": _fmt(robust_expected_return),
        "ev": _fmt(current_ev),
        "ev_current": _fmt(current_ev),
        "robust_ev": _fmt(robust_ev),
        "ev_predicted": _fmt(robust_ev),
        "official_odds_current": _fmt(current_ev / any_hit_probability),
        "win_odds": _fmt(current_ev / any_hit_probability),
        "robust_odds": _fmt(robust_ev / any_hit_probability),
        "predicted_odds": _fmt(robust_ev / any_hit_probability),
        "odds_source": "jra_live",
        "official_odds_complete": True,
        "probability_source": "04_ev_calculator",
        "source_candidate_key": f"{race_id}|wide_box|{'-'.join(numbers)}",
        "source_candidate_keys": [str(point["source_candidate_key"]) for point in points],
        "confidence": _fmt(current_ev),
        "minimum_positive_odds_sum": _fmt(outcome_audit["minimum_positive_odds_sum"]),
        "maximum_odds_sum": _fmt(outcome_audit["maximum_odds_sum"]),
        "minimum_positive_return": int(outcome_audit["minimum_positive_return"]),
        "maximum_return": int(outcome_audit["maximum_return"]),
        "minimum_positive_return_robust": int(
            outcome_audit["minimum_positive_return_robust"]
        ),
        "maximum_return_robust": int(outcome_audit["maximum_return_robust"]),
        "gami_probability_current": _fmt(outcome_audit["gami_probability_current"]),
        "gami_probability_robust": _fmt(outcome_audit["gami_probability_robust"]),
        "strict_no_gami_current": bool(outcome_audit["strict_no_gami_current"]),
        "strict_no_gami_robust": bool(outcome_audit["strict_no_gami_robust"]),
        "strict_no_gami": bool(outcome_audit["strict_no_gami"]),
        "top3_outcome_audit": outcome_audit,
        "probability_lineage": {
            "source": "04_ev_calculator",
            "candidate_key": f"{race_id}|wide_box|{'-'.join(numbers)}",
            "formula": "sum_pair_wide_prob_minus_2_sum_selected_triple_prob",
            "pair_probability_sum": pair_probability_sum,
            "selected_triple_probability_sum": three_hit_probability,
            "raw_hit_prob": any_hit_probability,
            "tolerance": 1e-9,
        },
    }

def _wide_box_outcome_audit(
    points: list[dict[str, object]],
    race_rows: list[dict[str, object]],
    *,
    total_stake: int,
    stake_per_point: int,
) -> dict[str, object]:
    current_positive_returns: list[float] = []
    robust_positive_returns: list[float] = []
    current_gami_probability = 0.0
    robust_gami_probability = 0.0
    hit_probability = 0.0
    three_hit_probability = 0.0
    outcome_probability_mass = 0.0
    for top3 in combinations(race_rows, 3):
        probability = _combo_hit_prob(list(top3), key="win_prob", bet_type="sanrenpuku")
        outcome_probability_mass += probability
        numbers = {str(row.get("horse_number", "")).strip() for row in top3}
        hits = [
            point
            for point in points
            if set(str(value) for value in list(point.get("horse_numbers") or [])) <= numbers
        ]
        if not hits:
            continue
        hit_probability += probability
        if len(hits) == 3:
            three_hit_probability += probability
        current_return = stake_per_point * sum(_to_float(point.get("odds")) for point in hits)
        robust_return = stake_per_point * sum(
            _to_float(point.get("robust_odds")) for point in hits
        )
        current_positive_returns.append(current_return)
        robust_positive_returns.append(robust_return)
        if current_return < total_stake:
            current_gami_probability += probability
        if robust_return < total_stake:
            robust_gami_probability += probability

    minimum_current = min(current_positive_returns, default=0.0)
    maximum_current = max(current_positive_returns, default=0.0)
    minimum_robust = min(robust_positive_returns, default=0.0)
    maximum_robust = max(robust_positive_returns, default=0.0)
    return {
        "outcome_probability_mass": _fmt(outcome_probability_mass),
        "hit_probability": _fmt(hit_probability),
        "three_hit_probability": _fmt(three_hit_probability),
        "gami_probability_current": current_gami_probability,
        "gami_probability_robust": robust_gami_probability,
        "gami_probability_current_given_hit": _fmt(
            current_gami_probability / hit_probability if hit_probability > 0.0 else 0.0
        ),
        "gami_probability_robust_given_hit": _fmt(
            robust_gami_probability / hit_probability if hit_probability > 0.0 else 0.0
        ),
        "minimum_positive_odds_sum": minimum_current / stake_per_point,
        "maximum_odds_sum": maximum_current / stake_per_point,
        "minimum_positive_return": int(minimum_current),
        "maximum_return": int(maximum_current),
        "minimum_positive_return_robust": int(minimum_robust),
        "maximum_return_robust": int(maximum_robust),
        "strict_no_gami_current": bool(current_positive_returns)
        and minimum_current >= total_stake,
        "strict_no_gami_robust": bool(robust_positive_returns)
        and minimum_robust >= total_stake,
        "strict_no_gami": bool(current_positive_returns)
        and bool(robust_positive_returns)
        and minimum_current >= total_stake
        and minimum_robust >= total_stake,
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


