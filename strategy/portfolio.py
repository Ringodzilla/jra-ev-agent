from __future__ import annotations


Ticket = dict[str, object]


def portfolio_ev(tickets: list[Ticket]) -> float:
    total_stake = portfolio_total_stake(tickets)
    return portfolio_expected_return(tickets) / total_stake if total_stake > 0 else 0.0


def portfolio_expected_return(tickets: list[Ticket]) -> float:
    return sum(
        int(_to_float(ticket.get("stake"))) * _to_float(ticket.get("ev_current") or ticket.get("ev"))
        for ticket in tickets
    )


def portfolio_no_gami(tickets: list[Ticket]) -> bool:
    total_stake = portfolio_total_stake(tickets)
    return total_stake > 0 and all(ticket_return_if_hit(ticket) >= total_stake for ticket in tickets)


def portfolio_total_stake(tickets: list[Ticket]) -> int:
    return sum(int(_to_float(ticket.get("stake"))) for ticket in tickets)


def portfolio_total_points(tickets: list[Ticket]) -> int:
    return sum(ticket_point_count(ticket) for ticket in tickets)


def ticket_return_if_hit(ticket: Ticket) -> int:
    if is_formation_ticket(ticket):
        stake_per_point = int(_to_float(ticket.get("stake_per_point")))
        odds = _to_float(ticket.get("trifecta_odds_min") or ticket.get("win_odds"))
        return int(stake_per_point * odds)
    stake = int(_to_float(ticket.get("stake")))
    odds = _to_float(ticket.get("win_odds") or ticket.get("predicted_odds"))
    return int(stake * odds)


def ticket_max_return_if_hit(ticket: Ticket) -> int:
    if is_formation_ticket(ticket):
        stake = int(_to_float(ticket.get("stake_per_point")))
        odds = _to_float(ticket.get("trifecta_odds_max") or ticket.get("win_odds"))
        return int(stake * odds)
    return ticket_return_if_hit(ticket)


def ticket_point_count(ticket: Ticket) -> int:
    if not is_formation_ticket(ticket):
        return 1
    point_count = int(_to_float(ticket.get("point_count")))
    return point_count if point_count > 0 else max(1, len(list(ticket.get("points") or [])))


def ticket_stake_unit(ticket: Ticket) -> int:
    return max(100, ticket_point_count(ticket) * 100) if is_formation_ticket(ticket) else 100


def with_adjusted_stake(ticket: Ticket, stake: int) -> Ticket:
    out = dict(ticket)
    adjusted = int(stake / ticket_stake_unit(ticket)) * ticket_stake_unit(ticket)
    out["stake"] = max(0, adjusted)
    if is_formation_ticket(out):
        stake_per_point = int(out["stake"] / max(ticket_point_count(out), 1))
        out["stake_per_point"] = stake_per_point
        min_odds = _to_float(out.get("trifecta_odds_min") or out.get("win_odds"))
        max_odds = _to_float(out.get("trifecta_odds_max") or out.get("win_odds"))
        out["min_return_if_hit"] = int(stake_per_point * min_odds)
        out["max_return_if_hit"] = int(stake_per_point * max_odds)
    return out


def portfolio_summary(tickets: list[Ticket]) -> dict[str, object]:
    total_stake = portfolio_total_stake(tickets)
    expected_return = portfolio_expected_return(tickets)
    return {
        "total_stake": total_stake,
        "total_points": portfolio_total_points(tickets),
        "expected_return": int(expected_return),
        "expected_profit": int(expected_return - total_stake),
        "portfolio_ev": _fmt(portfolio_ev(tickets)),
        "no_gami": portfolio_no_gami(tickets) if tickets else False,
    }


def is_formation_ticket(ticket: Ticket) -> bool:
    return str(ticket.get("ticket_shape", "")) == "formation"


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
