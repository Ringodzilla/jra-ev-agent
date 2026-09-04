from __future__ import annotations

import math
from itertools import combinations, product

from strategy.betting import MIN_ACTIONABLE_WIN_EV
from strategy.live_odds import build_live_odds_lookup, live_odds_value, lookup_live_odds
from strategy.portfolio import (
    portfolio_ev,
    portfolio_expected_return,
    portfolio_no_gami,
    portfolio_summary,
    portfolio_total_points,
    portfolio_total_stake,
    ticket_return_if_hit,
    ticket_stake_unit,
    with_adjusted_stake,
)

from src.agents.settings import WorkflowSettings


class ReviewerAgent:
    def __init__(self, settings: WorkflowSettings) -> None:
        self.settings = settings

    def run(
        self,
        collected: dict[str, object],
        scenario_rows: list[dict[str, object]],
        ev_rows: list[dict[str, object]],
        ticket_plan: dict[str, object],
        *,
        attempt: int,
    ) -> dict[str, object]:
        quality_report = dict(collected.get("quality_report") or {})
        entry_rows = list(collected.get("entries") or [])
        tickets = list(ticket_plan.get("tickets") or [])
        repair_enabled = bool(ticket_plan.get("reviewer_ticket_repair_enabled"))
        repaired_plan = bool(ticket_plan.get("reviewer_ticket_repair_applied"))

        reasons: list[str] = []
        repair_actions: list[object] = []
        ticket_repair_blocked = False
        stake_dependency_ratio = 0.0

        value_integrity_errors = _ticket_value_integrity_errors(tickets, ticket_plan)
        if value_integrity_errors:
            reasons.append(
                "ticket value integrity invalid: " + ", ".join(value_integrity_errors[:3])
            )
            ticket_repair_blocked = True

        high_issues = int(dict(quality_report.get("issues_by_severity") or {}).get("high", 0))
        if high_issues > 0:
            reasons.append(f"high severity parser issues: {high_issues}")
            ticket_repair_blocked = True
            if attempt < self.settings.max_repair_attempts:
                repair_actions.append("retry_aggressive_parse")

        missing_odds = int(quality_report.get("missing_current_odds_entries", 0) or 0)
        if entry_rows and missing_odds == len(entry_rows):
            reasons.append("current odds are missing for every entry")
            ticket_repair_blocked = True
            if attempt < self.settings.max_repair_attempts:
                repair_actions.append("retry_aggressive_parse")

        prob_sums = _probability_sums(ev_rows)
        bad_prob_races = [race_id for race_id, total in prob_sums.items() if abs(total - 1.0) > 0.025]
        if bad_prob_races:
            reasons.append(f"probability normalization drift detected: {bad_prob_races}")
            ticket_repair_blocked = True

        if str(ticket_plan.get("bet_type", "")) == "win5":
            ticket_repair_blocked = True
            win5_points = int(_to_float(ticket_plan.get("points"), 0.0))
            if win5_points <= 0:
                reasons.append("WIN5 formation has no valid points")
            if self.settings.win5_max_points is not None and win5_points > self.settings.win5_max_points:
                reasons.append("WIN5 formation exceeds max point constraint")
            if len(list(ticket_plan.get("legs") or [])) != 5:
                reasons.append("WIN5 formation must contain exactly five legs")
            configured_order = _race_order_from_configs(list(collected.get("race_configs") or []))
            actual_order = [str(race_id) for race_id in list(ticket_plan.get("race_order") or [])]
            if configured_order and actual_order[: len(configured_order)] != configured_order:
                reasons.append("WIN5 race order does not match config order")
        else:
            risky_tickets = [
                ticket
                for ticket in tickets
                if _ticket_ev(ticket, default=0.0) < _ticket_min_ev(ticket, self.settings)
                or _ticket_hit_prob(ticket) < _ticket_min_prob(ticket)
            ]
            if risky_tickets:
                reasons.append("ticket plan contains low-confidence or sub-threshold tickets")

            top_rows = sorted(
                ev_rows,
                key=lambda row: _to_float(row.get("win_prob"), 0.0),
                reverse=True,
            )[:3]
            ticket_horses = _ticket_horse_numbers(tickets)
            covered_top = [
                str(row.get("horse_number", ""))
                for row in top_rows
                if str(row.get("horse_number", "")) in ticket_horses
            ]
            top1_missing = bool(
                tickets
                and top_rows
                and str(top_rows[0].get("horse_number", "")) not in ticket_horses
            )
            if top1_missing and not repaired_plan:
                reasons.append("top win-probability horse is missing from every ticket")
            required_top_coverage = min(self.settings.min_top3_ticket_coverage, len(top_rows))
            if tickets and len(covered_top) < required_top_coverage:
                reasons.append(
                    f"top-3 ticket coverage is too low: {len(covered_top)}/{required_top_coverage}"
                )
            dependency_ratio = _max_horse_ticket_dependency_ratio(tickets)
            if tickets and dependency_ratio > self.settings.max_horse_ticket_dependency_ratio:
                reasons.append(
                    f"horse ticket dependency ratio is too high: {dependency_ratio:.3f}"
                )
            stake_dependency_ratio = _max_non_core_horse_stake_dependency_ratio(
                tickets,
                ev_rows,
            )
            if (
                tickets
                and stake_dependency_ratio > self.settings.max_horse_stake_dependency_ratio
            ):
                reasons.append(
                    "horse stake dependency ratio is too high: "
                    f"{stake_dependency_ratio:.3f}"
                )

            if tickets and portfolio_ev(tickets) < self.settings.min_portfolio_ev:
                reasons.append("ticket portfolio EV is below the configured minimum")
            if tickets and not portfolio_no_gami(tickets):
                reasons.append("ticket portfolio contains loss-on-hit tickets")

        if str(ticket_plan.get("bet_type", "")) != "win5":
            longshot_overweight = [
                ticket
                for ticket in tickets
                if _ticket_odds(ticket) >= _longshot_odds_threshold(ticket)
                and int(_to_float(ticket.get("stake"), 0.0)) > _longshot_stake_threshold(ticket)
            ]
            if longshot_overweight:
                reasons.append("ticket plan overweights extreme longshots")

        divergent_rows = _find_divergent_rows(
            ev_rows,
            max_ev_delta_abs=self.settings.max_ev_delta_abs,
            max_ev_delta_ratio=self.settings.max_ev_delta_ratio,
            max_odds_gap_ratio=self.settings.max_odds_gap_ratio,
        )
        selected_divergent_rows = _selected_divergent_rows(tickets, divergent_rows)
        actionable_divergent_rows = selected_divergent_rows if repaired_plan else divergent_rows
        if actionable_divergent_rows and str(ticket_plan.get("bet_type", "")) != "win5":
            reasons.append(
                "predicted/current EV divergence detected: "
                + ", ".join(
                    f"{row['horse_name']}@{row['race_id']}"
                    for row in actionable_divergent_rows[:3]
                )
            )

        missing_eligible_win_candidates = _find_missing_eligible_win_candidates(
            collected,
            ev_rows,
            ticket_plan,
            minimum_win_ev=max(self.settings.min_ev, MIN_ACTIONABLE_WIN_EV),
        )
        if missing_eligible_win_candidates and str(ticket_plan.get("bet_type", "")) != "win5":
            reasons.append(
                "eligible official-live win candidates are missing from candidate universe: "
                + ", ".join(
                    f"{row['horse_name']}@{row['race_id']}"
                    for row in missing_eligible_win_candidates[:3]
                )
            )

        if repair_enabled and tickets and reasons and not ticket_repair_blocked:
            ticket_repair = _build_ticket_repair_action(
                tickets,
                ev_rows=ev_rows,
                divergent_rows=divergent_rows,
                settings=self.settings,
            )
            if ticket_repair:
                repair_actions.append(ticket_repair)

        status = "OK" if not reasons else "NG"
        return {
            "status": status,
            "reason": "; ".join(reasons) if reasons else "quality gates passed",
            "fix": "; ".join(_repair_action_name(action) for action in repair_actions),
            "repair_actions": repair_actions,
            "probability_sums": {race_id: _fmt(total) for race_id, total in prob_sums.items()},
            "divergent_rows": divergent_rows,
            "selected_divergent_rows": selected_divergent_rows,
            "missing_eligible_win_candidates": missing_eligible_win_candidates,
            "horse_stake_dependency_ratio": _fmt(stake_dependency_ratio),
            "max_horse_stake_dependency_ratio": _fmt(
                self.settings.max_horse_stake_dependency_ratio
            ),
            "horse_stake_dependency_scope": "outside_top3_win_probability",
            "value_integrity": {
                "status": "NG" if value_integrity_errors else "OK",
                "errors": value_integrity_errors,
            },
            "stage_counts": {
                "entries": len(entry_rows),
                "feature_rows": len(scenario_rows),
                "ev_rows": len(ev_rows),
                "tickets": len(tickets),
            },
        }


def _probability_sums(ev_rows: list[dict[str, object]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in ev_rows:
        race_id = str(row.get("race_id", ""))
        totals[race_id] = totals.get(race_id, 0.0) + _to_float(row.get("win_prob"))
    return totals


def apply_ticket_repair_actions(
    ticket_plan: dict[str, object],
    repair_actions: list[object],
) -> dict[str, object]:
    action = next(
        (
            dict(value)
            for value in repair_actions
            if isinstance(value, dict) and value.get("action") == "filter_and_reallocate_tickets"
        ),
        None,
    )
    if action is None:
        return ticket_plan

    kept_stakes = {
        str(row.get("ticket_key", "")): int(_to_float(row.get("stake")))
        for row in list(action.get("kept_tickets") or [])
        if str(row.get("ticket_key", ""))
    }
    original_tickets = [dict(ticket) for ticket in list(ticket_plan.get("tickets") or [])]
    repaired = [
        with_adjusted_stake(ticket, kept_stakes[_ticket_repair_key(ticket)])
        for ticket in original_tickets
        if _ticket_repair_key(ticket) in kept_stakes
    ]
    repaired = _annotate_repaired_portfolio(repaired)
    removed = [
        ticket
        for ticket in original_tickets
        if _ticket_repair_key(ticket) not in kept_stakes
    ]

    out = dict(ticket_plan)
    out["tickets"] = repaired
    out["invalidated_tickets"] = removed
    out["portfolio_summary"] = portfolio_summary(repaired)
    out["ticket_status"] = "repaired_by_reviewer"
    out["reviewer_ticket_repair_enabled"] = False
    out["reviewer_ticket_repair_applied"] = True
    out["repair_action"] = action
    out["unused_bankroll"] = int(action.get("unused_bankroll", 0) or 0)
    out["primary_bet_type"] = str(repaired[0].get("bet_type", "")) if repaired else ""

    label_fields = {
        "win": "tansho",
        "place": "fukusho",
        "wide": "wide",
        "wakuren": "wakuren",
        "umaren": "umaren",
        "umatan": "umatan",
        "sanrenpuku": "sanrenpuku",
        "sanrentan": "sanrentan",
    }
    for bet_type, field in label_fields.items():
        out[field] = [
            str(ticket.get("horse_name", ""))
            for ticket in repaired
            if str(ticket.get("bet_type", "")) == bet_type
        ]

    repaired_races: list[dict[str, object]] = []
    for race in list(ticket_plan.get("races") or []):
        race_out = dict(race)
        race_original = [dict(ticket) for ticket in list(race_out.get("tickets") or [])]
        race_tickets = [
            with_adjusted_stake(ticket, kept_stakes[_ticket_repair_key(ticket)])
            for ticket in race_original
            if _ticket_repair_key(ticket) in kept_stakes
        ]
        race_tickets = _annotate_repaired_portfolio(race_tickets)
        race_out["tickets"] = race_tickets
        race_out["invalidated_tickets"] = [
            ticket
            for ticket in race_original
            if _ticket_repair_key(ticket) not in kept_stakes
        ]
        race_out["portfolio"] = portfolio_summary(race_tickets)
        race_out["ticket_status"] = "repaired_by_reviewer"
        repaired_races.append(race_out)
    out["races"] = repaired_races
    return out


def _build_ticket_repair_action(
    tickets: list[dict[str, object]],
    *,
    ev_rows: list[dict[str, object]],
    divergent_rows: list[dict[str, str]],
    settings: WorkflowSettings,
) -> dict[str, object] | None:
    mandatory_removed = {
        _ticket_repair_key(ticket)
        for ticket in tickets
        if _ticket_ev(ticket, default=0.0) < _ticket_min_ev(ticket, settings)
        or _ticket_hit_prob(ticket) < _ticket_min_prob(ticket)
        or (
            _ticket_odds(ticket) >= _longshot_odds_threshold(ticket)
            and int(_to_float(ticket.get("stake"))) > _longshot_stake_threshold(ticket)
        )
        or _ticket_matches_divergent_row(ticket, divergent_rows)
    }
    eligible = [
        dict(ticket)
        for ticket in tickets
        if _ticket_repair_key(ticket) not in mandatory_removed
    ]
    if not eligible:
        return None

    best: tuple[tuple[float, float, int, int], list[dict[str, object]]] | None = None
    for size in range(1, len(eligible) + 1):
        for subset_values in combinations(eligible, size):
            subset = [dict(ticket) for ticket in subset_values]
            stake_options = [
                range(
                    ticket_stake_unit(ticket),
                    _review_max_ticket_stake(ticket, settings.bankroll_per_race)
                    + ticket_stake_unit(ticket),
                    ticket_stake_unit(ticket),
                )
                for ticket in subset
            ]
            for stakes in product(*stake_options):
                allocated = [
                    with_adjusted_stake(ticket, stake)
                    for ticket, stake in zip(subset, stakes)
                ]
                if portfolio_total_stake(allocated) > settings.bankroll_per_race:
                    continue
                if not _repair_portfolio_safe(allocated, ev_rows=ev_rows, settings=settings):
                    continue
                expected_profit = portfolio_expected_return(allocated) - portfolio_total_stake(allocated)
                score = (
                    expected_profit,
                    portfolio_ev(allocated),
                    portfolio_total_stake(allocated),
                    len(allocated),
                )
                if best is None or score > best[0]:
                    best = (score, allocated)

    if best is None:
        return None

    repaired = best[1]
    kept_keys = {_ticket_repair_key(ticket) for ticket in repaired}
    if kept_keys == {_ticket_repair_key(ticket) for ticket in tickets} and all(
        int(_to_float(original.get("stake"))) == int(_to_float(replacement.get("stake")))
        for original in tickets
        for replacement in repaired
        if _ticket_repair_key(original) == _ticket_repair_key(replacement)
    ):
        return None

    total_stake = portfolio_total_stake(repaired)
    return {
        "action": "filter_and_reallocate_tickets",
        "kept_tickets": [
            {
                "ticket_key": _ticket_repair_key(ticket),
                "stake": int(_to_float(ticket.get("stake"))),
            }
            for ticket in repaired
        ],
        "removed_ticket_keys": [
            _ticket_repair_key(ticket)
            for ticket in tickets
            if _ticket_repair_key(ticket) not in kept_keys
        ],
        "mandatory_removed_ticket_keys": sorted(mandatory_removed),
        "pre_repair_ticket_count": len(tickets),
        "post_repair_ticket_count": len(repaired),
        "post_repair_total_stake": total_stake,
        "unused_bankroll": max(0, settings.bankroll_per_race - total_stake),
        "post_repair_portfolio_ev": _fmt(portfolio_ev(repaired)),
        "post_repair_no_gami": portfolio_no_gami(repaired),
        "post_repair_dependency_ratio": _fmt(_max_horse_ticket_dependency_ratio(repaired)),
        "post_repair_stake_dependency_ratio": _fmt(
            _max_non_core_horse_stake_dependency_ratio(repaired, ev_rows)
        ),
    }


def _repair_portfolio_safe(
    tickets: list[dict[str, object]],
    *,
    ev_rows: list[dict[str, object]],
    settings: WorkflowSettings,
) -> bool:
    if not tickets or portfolio_total_stake(tickets) <= 0:
        return False
    if portfolio_ev(tickets) < settings.min_portfolio_ev or not portfolio_no_gami(tickets):
        return False
    if _max_horse_ticket_dependency_ratio(tickets) > settings.max_horse_ticket_dependency_ratio:
        return False
    if (
        _max_non_core_horse_stake_dependency_ratio(tickets, ev_rows)
        > settings.max_horse_stake_dependency_ratio
    ):
        return False
    if any(
        _ticket_ev(ticket, default=0.0) < _ticket_min_ev(ticket, settings)
        or _ticket_hit_prob(ticket) < _ticket_min_prob(ticket)
        or (
            _ticket_odds(ticket) >= _longshot_odds_threshold(ticket)
            and int(_to_float(ticket.get("stake"))) > _longshot_stake_threshold(ticket)
        )
        for ticket in tickets
    ):
        return False

    top_rows = sorted(
        ev_rows,
        key=lambda row: _to_float(row.get("win_prob")),
        reverse=True,
    )[:3]
    ticket_horses = _ticket_horse_numbers(tickets)
    covered_top = sum(
        1
        for row in top_rows
        if str(row.get("horse_number", "")) in ticket_horses
    )
    return covered_top >= min(settings.min_top3_ticket_coverage, len(top_rows))


def _review_max_ticket_stake(ticket: dict[str, object], bankroll: int) -> int:
    bet_type = str(ticket.get("bet_type", ""))
    if str(ticket.get("ticket_shape", "")) == "formation":
        share = 0.70
    elif bet_type in {"place", "wide"}:
        share = 0.45
    elif bet_type == "win":
        share = 0.35
    elif bet_type in {"wakuren", "umaren"}:
        share = 0.30
    elif bet_type in {"umatan", "sanrenpuku"}:
        share = 0.24
    else:
        share = 0.20
    unit = ticket_stake_unit(ticket)
    return max(unit, int((bankroll * share) / unit) * unit)


def _annotate_repaired_portfolio(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    summary = portfolio_summary(tickets)
    total_stake = int(summary["total_stake"])
    total_points = portfolio_total_points(tickets)
    no_gami = bool(summary["no_gami"])
    annotated: list[dict[str, object]] = []
    for ticket in tickets:
        out = dict(ticket)
        return_if_hit = ticket_return_if_hit(out)
        out.update(
            {
                "portfolio_total_stake": total_stake,
                "portfolio_total_points": total_points,
                "portfolio_ev": summary["portfolio_ev"],
                "portfolio_expected_return": summary["expected_return"],
                "portfolio_expected_profit": summary["expected_profit"],
                "portfolio_no_gami": no_gami,
                "return_if_hit": return_if_hit,
                "net_return_if_hit": return_if_hit - total_stake,
            }
        )
        annotated.append(out)
    return annotated


def _selected_divergent_rows(
    tickets: list[dict[str, object]],
    divergent_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    return [
        row
        for row in divergent_rows
        if any(_ticket_matches_divergent_row(ticket, [row]) for ticket in tickets)
    ]


def _ticket_matches_divergent_row(
    ticket: dict[str, object],
    divergent_rows: list[dict[str, str]],
) -> bool:
    ticket_ids = {str(ticket.get("horse_id", "")).strip()}
    ticket_ids.update(str(value).strip() for value in list(ticket.get("horse_ids") or []))
    ticket_numbers = _ticket_horse_numbers([ticket])
    return any(
        (
            str(row.get("horse_id", "")).strip()
            and str(row.get("horse_id", "")).strip() in ticket_ids
        )
        or (
            str(row.get("horse_number", "")).strip()
            and str(row.get("horse_number", "")).strip() in ticket_numbers
        )
        for row in divergent_rows
    )


def _ticket_repair_key(ticket: dict[str, object]) -> str:
    race_id = str(ticket.get("race_id", "")).strip()
    bet_type = str(ticket.get("bet_type", "")).strip()
    values = [str(value).strip() for value in list(ticket.get("horse_numbers") or [])]
    if not values:
        values = [str(value).strip() for value in list(ticket.get("frame_numbers") or [])]
    if not values and ticket.get("horse_number") not in (None, ""):
        values = [str(ticket.get("horse_number", "")).strip()]
    return f"{race_id}|{bet_type}|{'-'.join(values)}"


def _repair_action_name(action: object) -> str:
    if isinstance(action, dict):
        return str(action.get("action", "repair"))
    return str(action)


def _find_missing_eligible_win_candidates(
    collected: dict[str, object],
    ev_rows: list[dict[str, object]],
    ticket_plan: dict[str, object],
    *,
    minimum_win_ev: float,
) -> list[dict[str, str]]:
    """Audit live win-EV rows against the bet builder's explicit candidate universe.

    Legacy plans did not expose candidate metadata. Those plans are intentionally
    skipped because an empty or absent legacy ticket list is not evidence that the
    builder evaluated every horse.
    """
    candidate_tokens_by_race: dict[str, set[str]] = {}
    metadata_races: set[str] = set()
    plan_races = [dict(race) for race in list(ticket_plan.get("races") or [])]

    for race in plan_races:
        race_id = str(race.get("race_id", "")).strip()
        if not race_id or "candidates" not in race:
            continue
        metadata_races.add(race_id)
        candidate_tokens_by_race.setdefault(race_id, set()).update(
            _win_candidate_tokens(list(race.get("candidates") or []), race_id=race_id)
        )
        if "candidate_evaluations" in race:
            candidate_tokens_by_race[race_id].update(
                _win_candidate_tokens(
                    list(race.get("candidate_evaluations") or []),
                    race_id=race_id,
                )
            )

    if "candidate_evaluations" in ticket_plan:
        default_race_id = str(ticket_plan.get("race_id", "")).strip()
        if not default_race_id and len(plan_races) == 1:
            default_race_id = str(plan_races[0].get("race_id", "")).strip()
        for evaluation in list(ticket_plan.get("candidate_evaluations") or []):
            candidate = dict(evaluation)
            race_id = str(candidate.get("race_id", "")).strip() or default_race_id
            if not race_id:
                continue
            metadata_races.add(race_id)
            candidate_tokens_by_race.setdefault(race_id, set()).update(
                _win_candidate_tokens([candidate], race_id=race_id)
            )

    if not metadata_races:
        return []

    live_odds_by_race = build_live_odds_lookup(list(collected.get("combo_odds") or []))
    if not live_odds_by_race:
        return []

    missing: list[dict[str, str]] = []
    for row in ev_rows:
        race_id = str(row.get("race_id", "")).strip()
        horse_number = _horse_number_token(row.get("horse_number"))
        if race_id not in metadata_races or not horse_number:
            continue
        live_row = lookup_live_odds(
            live_odds_by_race.get(race_id, {}),
            "win",
            [horse_number],
        )
        official_odds = live_odds_value(live_row)
        win_prob = _to_float(row.get("win_prob"))
        win_ev = win_prob * official_odds
        if official_odds <= 0 or win_prob <= 0 or win_ev < minimum_win_ev:
            continue

        row_tokens = {f"horse_number:{horse_number}"}
        horse_id = str(row.get("horse_id", "")).strip()
        if horse_id:
            row_tokens.add(f"horse_id:{horse_id}")
        if row_tokens & candidate_tokens_by_race.get(race_id, set()):
            continue

        missing.append(
            {
                "race_id": race_id,
                "horse_id": horse_id,
                "horse_number": horse_number,
                "horse_name": str(row.get("horse_name", "")).strip(),
                "win_prob": _fmt(win_prob),
                "official_odds": _fmt(official_odds),
                "win_ev": _fmt(win_ev),
                "minimum_win_ev": _fmt(minimum_win_ev),
                "odds_source": "jra_live",
            }
        )

    return sorted(
        missing,
        key=lambda row: _to_float(row.get("win_ev")),
        reverse=True,
    )


def _win_candidate_tokens(candidates: list[object], *, race_id: str) -> set[str]:
    tokens: set[str] = set()
    for value in candidates:
        if not isinstance(value, dict):
            continue
        candidate = dict(value)
        if str(candidate.get("race_id", "")).strip() not in {"", race_id}:
            continue
        if str(candidate.get("bet_type", "")).strip() != "win":
            continue
        horse_number = _horse_number_token(
            candidate.get("horse_number") or candidate.get("combination")
        )
        if horse_number:
            tokens.add(f"horse_number:{horse_number}")
        horse_id = str(candidate.get("horse_id", "")).strip()
        if horse_id:
            tokens.add(f"horse_id:{horse_id}")
    return tokens


def _horse_number_token(value: object) -> str:
    number = int(_to_float(value, 0.0))
    return str(number) if number > 0 else ""


def _race_order_from_configs(race_configs: list[dict[str, object]]) -> list[str]:
    order: list[str] = []
    for config in race_configs:
        race_id = _race_id_from_config(config)
        if race_id:
            order.append(race_id)
    return order


def _race_id_from_config(config: dict[str, object]) -> str:
    explicit = str(config.get("race_id", "")).strip()
    if explicit:
        return explicit

    race_date = str(config.get("race_date", "")).replace("-", "").strip()
    track = str(config.get("track", "")).strip()
    race_number = str(config.get("race_number", "")).strip()
    if race_date and track and race_number:
        return f"{race_date}_{track}_{int(_to_float(race_number)):02d}"
    return ""


def _find_divergent_rows(
    ev_rows: list[dict[str, object]],
    *,
    max_ev_delta_abs: float,
    max_ev_delta_ratio: float,
    max_odds_gap_ratio: float,
) -> list[dict[str, str]]:
    divergent: list[dict[str, str]] = []
    for row in ev_rows:
        current_odds = _to_float(row.get("current_odds"))
        predicted_odds = _to_float(row.get("predicted_odds"))
        ev_current = _to_float(row.get("ev_current") or row.get("ev"))
        ev_predicted = _to_float(row.get("ev_predicted"))
        if current_odds <= 0 or predicted_odds <= 0 or ev_current <= 0 or ev_predicted <= 0:
            continue

        odds_gap_ratio = abs((predicted_odds - current_odds) / current_odds)
        ev_delta = abs(ev_predicted - ev_current)
        ev_delta_ratio = ev_delta / max(ev_current, 1e-6)
        thresholds = _thresholds_for_popularity(
            _to_float(row.get("popularity_latest") or row.get("current_popularity")),
            defaults={
                "max_ev_delta_abs": max_ev_delta_abs,
                "max_ev_delta_ratio": max_ev_delta_ratio,
                "max_odds_gap_ratio": max_odds_gap_ratio,
            },
        )
        if (
            ev_delta >= thresholds["max_ev_delta_abs"]
            or ev_delta_ratio >= thresholds["max_ev_delta_ratio"]
            or odds_gap_ratio >= thresholds["max_odds_gap_ratio"]
        ):
            divergent.append(
                {
                    "race_id": str(row.get("race_id", "")),
                    "horse_id": str(row.get("horse_id", "")),
                    "horse_number": str(row.get("horse_number", "")),
                    "horse_name": str(row.get("horse_name", "")),
                    "popularity_band": thresholds["band"],
                    "current_odds": _fmt(current_odds),
                    "predicted_odds": _fmt(predicted_odds),
                    "ev_current": _fmt(ev_current),
                    "ev_predicted": _fmt(ev_predicted),
                    "ev_delta_ratio": _fmt(ev_delta_ratio),
                    "odds_gap_ratio": _fmt(odds_gap_ratio),
                }
            )
    return divergent


def _thresholds_for_popularity(
    popularity: float,
    *,
    defaults: dict[str, float],
) -> dict[str, float | str]:
    if popularity > 0 and popularity <= 3:
        return {
            "band": "favorite",
            "max_ev_delta_abs": min(defaults["max_ev_delta_abs"], 0.12),
            "max_ev_delta_ratio": min(defaults["max_ev_delta_ratio"], 0.12),
            "max_odds_gap_ratio": min(defaults["max_odds_gap_ratio"], 0.15),
        }
    if popularity > 0 and popularity <= 8:
        return {
            "band": "mid",
            "max_ev_delta_abs": min(defaults["max_ev_delta_abs"], 0.20),
            "max_ev_delta_ratio": min(defaults["max_ev_delta_ratio"], 0.18),
            "max_odds_gap_ratio": min(defaults["max_odds_gap_ratio"], 0.25),
        }
    return {
        "band": "longshot",
        "max_ev_delta_abs": max(defaults["max_ev_delta_abs"], 0.28),
        "max_ev_delta_ratio": max(defaults["max_ev_delta_ratio"], 0.28),
        "max_odds_gap_ratio": max(defaults["max_odds_gap_ratio"], 0.36),
    }


def _ticket_hit_prob(ticket: dict[str, object]) -> float:
    return _to_float(ticket.get("hit_prob") or ticket.get("wide_prob") or ticket.get("win_prob"))


def _ticket_value_integrity_errors(
    tickets: list[dict[str, object]],
    ticket_plan: dict[str, object],
) -> list[str]:
    """Fail closed when official-live ticket values are not reproducible."""
    errors: list[str] = []
    for ticket in tickets:
        if str(ticket.get("odds_source", "")) != "jra_live":
            continue
        if str(ticket.get("ticket_shape", "")) == "formation":
            continue
        label = f"{ticket.get('bet_type', '')}:{ticket.get('horse_number', '')}"
        odds = _to_float(ticket.get("win_odds"), -1.0)
        probability = _to_float(ticket.get("hit_prob"), -1.0)
        ev_current = _to_float(ticket.get("ev_current"), -1.0)
        if (
            not all(math.isfinite(value) for value in (odds, probability, ev_current))
            or odds <= 0
            or not 0 < probability <= 1
            or ev_current <= 0
        ):
            errors.append(f"{label} missing canonical probability, odds, or EV")
            continue
        expected_ev = probability * odds
        tolerance = max(0.00001, abs(expected_ev) * 0.00001)
        if abs(ev_current - expected_ev) > tolerance:
            errors.append(
                f"{label} ev_current={_fmt(ev_current)} != "
                f"hit_prob*odds={_fmt(expected_ev)}"
            )
        displayed_ev = _to_float(ticket.get("ev"), -1.0)
        if not math.isfinite(displayed_ev) or displayed_ev <= 0:
            errors.append(f"{label} missing displayed EV")
            continue
        if abs(displayed_ev - ev_current) > tolerance:
            errors.append(
                f"{label} ev={_fmt(displayed_ev)} != ev_current={_fmt(ev_current)}"
            )

    declared = dict(ticket_plan.get("portfolio_summary") or {})
    if tickets and declared:
        canonical = portfolio_summary(tickets)
        for key in ("total_stake", "total_points", "expected_return", "expected_profit"):
            if int(_to_float(declared.get(key), -1)) != int(canonical[key]):
                errors.append(f"portfolio {key} does not match canonical recomputation")
        if abs(
            _to_float(declared.get("portfolio_ev"), -1.0)
            - _to_float(canonical.get("portfolio_ev"))
        ) > 0.00001:
            errors.append("portfolio EV does not match canonical recomputation")
    return errors


def _ticket_odds(ticket: dict[str, object]) -> float:
    return _to_float(
        ticket.get("place_odds_est")
        or ticket.get("wide_odds_est")
        or ticket.get("wakuren_odds_est")
        or ticket.get("umaren_odds_est")
        or ticket.get("umatan_odds_est")
        or ticket.get("trio_odds_est")
        or ticket.get("trifecta_odds_est")
        or ticket.get("predicted_wide_odds")
        or ticket.get("win_odds")
    )


def _ticket_ev(ticket: dict[str, object], *, default: float = 0.0) -> float:
    return _to_float(ticket.get("ev_current") or ticket.get("ev"), default)


def _ticket_min_prob(ticket: dict[str, object]) -> float:
    bet_type = str(ticket.get("bet_type", ""))
    if bet_type == "place":
        return 0.16
    if bet_type == "wide":
        return 0.10
    if bet_type == "wakuren":
        return 0.035
    if bet_type == "umaren":
        return 0.035
    if bet_type == "umatan":
        return 0.018
    if bet_type == "sanrenpuku":
        return 0.018
    if bet_type == "sanrentan":
        return 0.006
    return 0.04


def _ticket_min_ev(ticket: dict[str, object], settings: WorkflowSettings) -> float:
    bet_type = str(ticket.get("bet_type", ""))
    if bet_type == "place":
        return settings.min_place_ev
    if bet_type == "wide":
        return settings.min_wide_ev
    if bet_type == "wakuren":
        return settings.min_wakuren_ev
    if bet_type == "umaren":
        return settings.min_umaren_ev
    if bet_type == "umatan":
        return settings.min_umatan_ev
    if bet_type == "sanrenpuku":
        return settings.min_sanrenpuku_ev
    if bet_type == "sanrentan":
        return settings.min_sanrentan_ev
    return max(settings.min_ev, 1.05)


def _longshot_odds_threshold(ticket: dict[str, object]) -> float:
    bet_type = str(ticket.get("bet_type", ""))
    if bet_type == "place":
        return 8.0
    if bet_type == "wide":
        return 16.0
    if bet_type == "wakuren":
        return 35.0
    if bet_type == "umaren":
        return 35.0
    if bet_type == "umatan":
        return 70.0
    if bet_type == "sanrenpuku":
        return 60.0
    if bet_type == "sanrentan":
        return 120.0
    return 20.0


def _longshot_stake_threshold(ticket: dict[str, object]) -> int:
    bet_type = str(ticket.get("bet_type", ""))
    if bet_type in {"place", "wide"}:
        return 300
    if bet_type in {"wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan"}:
        return 100
    return 100


def _ticket_horse_numbers(tickets: list[dict[str, object]]) -> set[str]:
    numbers: set[str] = set()
    for ticket in tickets:
        explicit = [str(value) for value in list(ticket.get("horse_numbers") or [])]
        value = str(ticket.get("horse_number", "")).replace(">", "-").replace("→", "-")
        numbers.update(part for part in explicit + value.split("-") if part.isdigit())
    return numbers


def _max_horse_ticket_dependency_ratio(tickets: list[dict[str, object]]) -> float:
    if not tickets:
        return 0.0
    counts: dict[str, int] = {}
    for ticket in tickets:
        explicit = [str(value) for value in list(ticket.get("horse_numbers") or [])]
        value = str(ticket.get("horse_number", "")).replace(">", "-").replace("→", "-")
        for horse_number in set(part for part in explicit + value.split("-") if part.isdigit()):
            counts[horse_number] = counts.get(horse_number, 0) + 1
    return max(counts.values(), default=0) / len(tickets)


def _max_horse_stake_dependency_ratio(
    tickets: list[dict[str, object]],
    *,
    exempt_horse_numbers: set[str] | None = None,
) -> float:
    if len(tickets) <= 1:
        return 0.0
    total_stake = sum(int(_to_float(ticket.get("stake"))) for ticket in tickets)
    if total_stake <= 0:
        return 0.0
    stakes: dict[str, int] = {}
    exempt = exempt_horse_numbers or set()
    for ticket in tickets:
        stake = int(_to_float(ticket.get("stake")))
        explicit = [str(value) for value in list(ticket.get("horse_numbers") or [])]
        value = str(ticket.get("horse_number", "")).replace(">", "-").replace("→", "-")
        for horse_number in set(part for part in explicit + value.split("-") if part.isdigit()):
            if horse_number in exempt:
                continue
            stakes[horse_number] = stakes.get(horse_number, 0) + stake
    return max(stakes.values(), default=0) / total_stake


def _max_non_core_horse_stake_dependency_ratio(
    tickets: list[dict[str, object]],
    ev_rows: list[dict[str, object]],
) -> float:
    tickets_by_race: dict[str, list[dict[str, object]]] = {}
    rows_by_race: dict[str, list[dict[str, object]]] = {}
    for ticket in tickets:
        tickets_by_race.setdefault(str(ticket.get("race_id", "")), []).append(ticket)
    for row in ev_rows:
        rows_by_race.setdefault(str(row.get("race_id", "")), []).append(row)

    ratios: list[float] = []
    for race_id, race_tickets in tickets_by_race.items():
        top_rows = sorted(
            rows_by_race.get(race_id, []),
            key=lambda row: _to_float(row.get("win_prob")),
            reverse=True,
        )[:3]
        exempt_horse_numbers = {
            str(row.get("horse_number", "")).strip()
            for row in top_rows
            if str(row.get("horse_number", "")).strip()
        }
        ratios.append(
            _max_horse_stake_dependency_ratio(
                race_tickets,
                exempt_horse_numbers=exempt_horse_numbers,
            )
        )
    return max(ratios, default=0.0)


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
