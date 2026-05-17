from __future__ import annotations

from collections import defaultdict
from itertools import combinations, permutations
import math


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
            if _win_candidate_ev(row, live_odds) >= min_ev and _live_or_current_win_odds(row, live_odds) > 0
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

        win_tickets = [
            ticket
            for row in win_candidates[:per_race_limit]
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

        candidate_pool = _rank_ticket_pool(
            _dedupe_ticket_combos(
                win_tickets
                + place_candidates
                + wide_candidates
                + wakuren_candidates
                + exotic_candidates
            ),
            prefer_wide=prefer_wide,
        )
        candidate_references.extend(candidate_pool)
        candidate_counts = _candidate_counts_by_type(candidate_pool)
        for bet_type, count in candidate_counts.items():
            aggregate_candidate_counts[bet_type] += count

        selection_pool = _dedupe_ticket_combos(
            win_tickets
            + place_candidates
            + wide_candidates[:max_wide_tickets_per_race]
            + wakuren_candidates
            + _prioritize_exotic_types(exotic_candidates)[:max_exotic_tickets_per_race]
        )
        race_tickets = _select_optimized_tickets(
            selection_pool,
            per_race_limit=per_race_limit,
            prefer_wide=prefer_wide,
            force_win_standout=_has_win_standout(enriched),
        )
        race_tickets = _rebalance_race_stakes(race_tickets, bankroll_per_race=bankroll_per_race)
        flat_tickets.extend(race_tickets)

        place_ranked = sorted(
            enriched,
            key=lambda row: (_to_float(row.get("place_prob")), _to_float(row.get("win_prob"))),
            reverse=True,
        )
        race_core = [_horse_summary(row) for row in place_ranked[:2] if _to_float(row.get("place_prob")) >= 0.22]
        race_partner = [_horse_summary(row) for row in place_ranked[2:4] if _to_float(row.get("place_prob")) >= 0.16]
        race_long = [
            _horse_summary(row)
            for row in enriched
            if _to_float(row.get("current_odds")) >= 10.0 and _to_float(row.get("ev")) >= max(min_ev, 1.08)
        ][:2]

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
    if prob <= 0 or odds <= 1.0 or ev <= 1.0:
        return None

    full_kelly = ((odds * prob) - 1.0) / max(odds - 1.0, 1e-6)
    recommended_fraction = max(0.0, min(0.30, full_kelly * kelly_fraction))
    stake = int((bankroll_per_race * recommended_fraction) / 100) * 100
    if stake < 100:
        stake = 100 if ev >= 1.08 else 0
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
) -> dict[str, object] | None:
    pair_prob = _estimate_pair_hit_prob(left, right, field_size=field_size)
    market_pair_prob = _estimate_market_pair_prob(left, right, field_size=field_size)
    horse_numbers = [str(left.get("horse_number", "")), str(right.get("horse_number", ""))]
    live = _lookup_live_odds(live_odds, "wide", horse_numbers)
    current_odds_est = _live_odds_value(live) or _estimate_market_pair_odds(market_pair_prob)
    predicted_odds_est = _estimate_predicted_pair_odds(left, right, current_odds_est=current_odds_est)
    ev_current = pair_prob * current_odds_est if current_odds_est > 0 else 0.0
    ev_predicted = pair_prob * predicted_odds_est if predicted_odds_est > 0 else 0.0

    if pair_prob < 0.10 or current_odds_est <= 1.0 or ev_current < min_wide_ev:
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
        "confidence": _fmt(confidence),
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

    candidates.sort(
        key=lambda ticket: (
            _to_float(ticket.get("ev_current") or ticket.get("ev")),
            _to_float(ticket.get("hit_prob")),
            _to_float(ticket.get("confidence")),
        ),
        reverse=True,
    )

    return _prioritize_exotic_types(_dedupe_ticket_combos(candidates))


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
) -> dict[str, object] | None:
    hit_prob = _combo_hit_prob(combo_rows, key="win_prob", bet_type=bet_type)
    market_prob = _combo_hit_prob(combo_rows, key="market_prob", bet_type=bet_type)
    if hit_prob < min_prob or market_prob <= 0:
        return None

    horse_numbers = [str(row.get("horse_number", "")) for row in combo_rows]
    live = _lookup_live_odds(live_odds, bet_type, horse_numbers)
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
    }


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
    if ticket.get("bet_type") == "wakuren":
        frame_numbers = [str(value) for value in list(ticket.get("frame_numbers") or []) if str(value).strip()]
        return "-".join(frame_numbers) if frame_numbers else str(ticket.get("horse_number", ""))
    values = list(ticket.get("horse_names") or [])
    if not values:
        return str(ticket.get("horse_name", ""))
    return _combo_name([str(value) for value in values], bet_type=str(ticket.get("bet_type", "")))


def _dedupe_ticket_combos(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, object]] = []
    for ticket in tickets:
        key = (
            str(ticket.get("race_id", "")),
            str(ticket.get("bet_type", "")),
            str(ticket.get("horse_number", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ticket)
    return out


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


def _select_optimized_tickets(
    tickets: list[dict[str, object]],
    *,
    per_race_limit: int,
    prefer_wide: bool,
    force_win_standout: bool,
) -> list[dict[str, object]]:
    ranked = _rank_ticket_pool(_dedupe_ticket_combos(tickets), prefer_wide=prefer_wide)
    by_type: dict[str, list[dict[str, object]]] = defaultdict(list)
    for ticket in ranked:
        by_type[str(ticket.get("bet_type", ""))].append(ticket)

    type_order = list(_selection_type_order(prefer_wide=prefer_wide))
    selected: list[dict[str, object]] = []
    selected_keys: set[tuple[str, str]] = set()

    if force_win_standout and not prefer_wide and by_type["win"]:
        _append_ticket_if_new(selected, selected_keys, by_type["win"][0], per_race_limit)

    for bet_type in type_order:
        if len(selected) >= per_race_limit:
            break
        if not by_type[bet_type]:
            continue
        _append_ticket_if_new(selected, selected_keys, by_type[bet_type][0], per_race_limit)

    by_ev = sorted(
        ranked,
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
    selected.append(ticket)
    selected_keys.add(key)


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


def _build_live_odds_lookup(
    odds_rows: list[dict[str, object]] | list[dict[str, str]],
) -> dict[str, dict[tuple[str, str], dict[str, object]]]:
    by_race: dict[str, dict[tuple[str, str], dict[str, object]]] = defaultdict(dict)
    for row in odds_rows:
        race_id = str(row.get("race_id", "")).strip()
        bet_type = str(row.get("bet_type", "")).strip()
        combination = str(row.get("combination", "")).strip()
        if not race_id or not bet_type or not combination:
            continue
        key = (bet_type, combination)
        current = by_race[race_id].get(key)
        if current and str(current.get("captured_at", "")) > str(row.get("captured_at", "")):
            continue
        by_race[race_id][key] = dict(row)
    return by_race


def _lookup_live_odds(
    live_odds: dict[tuple[str, str], dict[str, object]],
    bet_type: str,
    values: list[str],
) -> dict[str, object]:
    key = (bet_type, _live_combo_key(bet_type, values))
    return dict(live_odds.get(key) or {})


def _live_combo_key(bet_type: str, values: list[str]) -> str:
    cleaned = [str(int(_to_float(value))) for value in values if _to_float(value) > 0]
    if not cleaned:
        return ""
    if bet_type in {"umatan", "sanrentan"}:
        return ">".join(cleaned)
    if bet_type in {"wide", "wakuren", "umaren", "sanrenpuku"}:
        return "-".join(sorted(cleaned, key=lambda item: int(item)))
    return cleaned[0]


def _live_odds_value(row: dict[str, object]) -> float:
    if not row:
        return 0.0
    return _to_float(row.get("odds_min") or row.get("odds"))


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
    return _to_float(leader.get("win_prob")) >= 0.20 and _to_float(leader.get("ev")) >= 1.08


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
        out = dict(ticket)
        stake = int(_to_float(ticket.get("stake"), 0.0))
        adjusted = int((stake * scale) / 100) * 100
        if adjusted <= 0:
            continue
        out["stake"] = adjusted
        scaled.append(out)

    if not scaled:
        best = dict(max(tickets, key=lambda ticket: _to_float(ticket.get("ev_current") or ticket.get("ev"))))
        best["stake"] = min(100, bankroll_per_race)
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
