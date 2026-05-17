from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.ev import compute_ev, load_rows
from strategy.betting import generate_tickets


def _compact_label(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s_\-・]+", "", normalized).lower()


BET_TYPES = ("win", "place", "wide", "wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan")
UNORDERED_BET_TYPES = {"wide", "wakuren", "umaren", "sanrenpuku"}
ORDERED_BET_TYPES = {"umatan", "sanrentan"}

BET_TYPE_ALIASES = {
    "win": ("win", "tansho", "単勝"),
    "place": ("place", "fukusho", "複勝"),
    "wide": ("wide", "ワイド"),
    "wakuren": ("wakuren", "枠連", "枠連複"),
    "umaren": ("umaren", "馬連", "馬連複"),
    "umatan": ("umatan", "馬単"),
    "sanrenpuku": ("sanrenpuku", "三連複", "3連複", "３連複"),
    "sanrentan": ("sanrentan", "三連単", "3連単", "３連単"),
}
ALIAS_TO_BET_TYPE = {
    _compact_label(alias): bet_type
    for bet_type, aliases in BET_TYPE_ALIASES.items()
    for alias in aliases
}
BET_TYPE_COLUMNS = ("bet_type", "ticket_type", "wager_type", "式別", "券種", "賭式")
RACE_ID_COLUMNS = ("race_id", "race", "レースID", "レース")
GENERIC_COMBO_COLUMNS = (
    "combination",
    "combo",
    "numbers",
    "horse_numbers",
    "horse_number",
    "frame_numbers",
    "frame_number",
    "ticket",
    "result",
    "組番",
    "馬番",
    "枠番",
)
GENERIC_PAYOUT_COLUMNS = (
    "payout_per_100",
    "payout",
    "return_per_100",
    "return",
    "dividend",
    "払戻金",
    "払戻",
    "配当",
)


def load_results(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        normalized = unicodedata.normalize("NFKC", str(value))
        normalized = normalized.replace(",", "").replace("円", "").strip()
        return float(normalized)
    except (TypeError, ValueError):
        return default


def _current_git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
        return out.strip()
    except Exception:
        return "unknown"


def decide_keep_or_revert(before: dict[str, float | int], after: dict[str, float | int]) -> tuple[str, str]:
    before_roi = float(before.get("validation_roi", before.get("roi", 0.0)))
    after_roi = float(after.get("validation_roi", after.get("roi", 0.0)))
    before_score = float(before.get("score", 0.0))
    after_score = float(after.get("score", 0.0))

    if after_roi > before_roi:
        return "keep", "validation ROI improved"
    if after_roi < before_roi:
        return "revert", "validation ROI decreased (primary metric regression)"
    if after_score > before_score:
        return "keep", "validation ROI tied and score improved"
    return "revert", "validation ROI tied and score did not improve"


def evaluate_strategy(
    rows: list[dict[str, str]],
    min_ev: float = 1.05,
    max_bets_per_race: int = 2,
    stake_per_bet: int = 100,
    *,
    results: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    scored = compute_ev(rows)
    ticket_plan = generate_tickets(
        scored,
        bankroll_per_race=max_bets_per_race * stake_per_bet,
        min_ev=min_ev,
        max_tickets_per_race=max_bets_per_race,
        prefer_wide=False,
    )
    tickets = list(ticket_plan.get("tickets") or [])

    if not results:
        return {
            "score": 0.0,
            "validation_roi": 0.0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "sharpe_like": 0.0,
            "max_drawdown": 0.0,
            "ticket_count": len(tickets),
            "race_count": len({str(row.get("race_id", "")) for row in scored}),
            "invested": sum(int(_to_float(ticket.get("stake"), 0.0)) for ticket in tickets),
            "returned": 0.0,
            "bet_type_breakdown": {},
            "hit_ticket_count": 0,
            "ticket_hit_rate": 0.0,
            "result_bet_types_available": [],
            "result_label_count": 0,
            "git_commit": _current_git_commit(),
            "label_status": "missing",
        }

    payout_lookup = _build_payout_lookup(results)
    available_bet_types = {bet_type for _, bet_type, _ in payout_lookup}
    result_bet_types_available = [bet_type for bet_type in BET_TYPES if bet_type in available_bet_types]
    ticket_bet_types = sorted({_normalize_bet_type(ticket.get("bet_type", "win")) or "win" for ticket in tickets})
    race_ids = {_first_nonempty(row, RACE_ID_COLUMNS) for row in results}
    race_ids = {race_id for race_id in race_ids if race_id}

    invested = 0
    returned = 0.0
    hit_races: set[str] = set()
    hit_ticket_count = 0
    pnls: list[float] = []
    by_type: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"ticket_count": 0, "hit_ticket_count": 0, "invested": 0, "returned": 0.0}
    )
    tickets_by_race: dict[str, list[dict[str, object]]] = defaultdict(list)
    for ticket in tickets:
        tickets_by_race[str(ticket.get("race_id", ""))].append(ticket)

    for race_id, race_tickets in tickets_by_race.items():
        race_invested = 0
        race_returned = 0.0
        for ticket in race_tickets:
            bet_type = _normalize_bet_type(ticket.get("bet_type", "win")) or "win"
            combo_key = _ticket_combo_key(ticket)
            stake = int(_to_float(ticket.get("stake"), 0.0))
            race_invested += stake
            by_type[bet_type]["ticket_count"] = int(by_type[bet_type]["ticket_count"]) + 1
            by_type[bet_type]["invested"] = int(by_type[bet_type]["invested"]) + stake
            payout_per_100 = payout_lookup.get((race_id, bet_type, combo_key), 0.0)
            ticket_returned = payout_per_100 * (stake / 100)
            race_returned += ticket_returned
            by_type[bet_type]["returned"] = float(by_type[bet_type]["returned"]) + ticket_returned
            if ticket_returned > 0:
                hit_ticket_count += 1
                by_type[bet_type]["hit_ticket_count"] = int(by_type[bet_type]["hit_ticket_count"]) + 1
        invested += race_invested
        returned += race_returned
        pnls.append(race_returned - race_invested)
        if race_returned > 0:
            hit_races.add(race_id)

    roi = (returned / invested) if invested > 0 else 0.0
    race_count = max(len(race_ids), len(tickets_by_race), 1)
    hit_rate = len(hit_races) / race_count
    ticket_hit_rate = hit_ticket_count / max(len(tickets), 1)

    if pnls:
        mean = sum(pnls) / len(pnls)
        var = sum((value - mean) ** 2 for value in pnls) / len(pnls)
        std = var ** 0.5
        sharpe_like = (mean / std) if std > 0 else 0.0
    else:
        sharpe_like = 0.0

    max_drawdown = _max_drawdown(pnls)
    ticket_penalty = min(len(tickets) / race_count, 10.0) * 0.02
    drawdown_penalty = min(max_drawdown / max(invested, 1), 1.0) * 0.10
    score = (
        0.70 * (min(max(roi, 0.0), 2.0) / 2.0)
        + 0.20 * hit_rate
        + 0.10 * (min(max(sharpe_like, -1.0), 3.0) / 3.0)
    ) - ticket_penalty - drawdown_penalty
    score = max(0.0, min(1.0, score))

    return {
        "score": score,
        "validation_roi": roi,
        "roi": roi,
        "hit_rate": hit_rate,
        "sharpe_like": sharpe_like,
        "max_drawdown": max_drawdown,
        "ticket_count": len(tickets),
        "hit_ticket_count": hit_ticket_count,
        "ticket_hit_rate": ticket_hit_rate,
        "race_count": race_count,
        "invested": invested,
        "returned": returned,
        "bet_type_breakdown": _bet_type_breakdown(by_type),
        "result_bet_types_available": result_bet_types_available,
        "result_label_count": len(payout_lookup),
        "git_commit": _current_git_commit(),
        "label_status": _label_status(payout_lookup, ticket_bet_types),
    }


def _build_payout_lookup(results: list[dict[str, str]]) -> dict[tuple[str, str, str], float]:
    lookup: dict[tuple[str, str, str], float] = {}
    for row in results:
        race_id = _first_nonempty(row, RACE_ID_COLUMNS)
        if not race_id:
            continue

        explicit_bet_type = _normalize_bet_type(_first_nonempty(row, BET_TYPE_COLUMNS))
        if explicit_bet_type:
            _add_payout_lookup_row(lookup, race_id, explicit_bet_type, row)
            continue

        for bet_type in BET_TYPES:
            _add_payout_lookup_row(lookup, race_id, bet_type, row, require_typed_payout=True)

    return lookup


def _add_payout_lookup_row(
    lookup: dict[tuple[str, str, str], float],
    race_id: str,
    bet_type: str,
    row: dict[str, str],
    *,
    require_typed_payout: bool = False,
) -> None:
    payout = _row_payout(row, bet_type, require_typed_payout=require_typed_payout)
    if payout <= 0:
        return
    combo = _row_combo_key(row, bet_type)
    if not combo:
        return
    lookup[(race_id, bet_type, combo)] = payout


def _row_payout(row: dict[str, str], bet_type: str, *, require_typed_payout: bool) -> float:
    columns = _typed_payout_columns(bet_type)
    if not require_typed_payout:
        columns = tuple(columns) + GENERIC_PAYOUT_COLUMNS
    for column in columns:
        value = _get_case_insensitive(row, column)
        payout = _to_float(value)
        if payout > 0:
            return payout
    return 0.0


def _row_combo_key(row: dict[str, str], bet_type: str) -> str:
    columns = _typed_combo_columns(bet_type) + GENERIC_COMBO_COLUMNS
    value = _first_nonempty(row, columns)
    return _normalize_combo_key(value, bet_type)


def _ticket_combo_key(ticket: dict[str, object]) -> str:
    bet_type = _normalize_bet_type(ticket.get("bet_type", "win")) or "win"
    if bet_type == "wakuren":
        values = list(ticket.get("frame_numbers") or [])
        if values:
            return _normalize_combo_key("-".join(str(value) for value in values), bet_type)
    if bet_type in {"wide", "umaren", "umatan", "sanrenpuku", "sanrentan"}:
        values = list(ticket.get("horse_numbers") or [])
        if values:
            separator = ">" if bet_type in ORDERED_BET_TYPES else "-"
            return _normalize_combo_key(separator.join(str(value) for value in values), bet_type)
    return _normalize_combo_key(ticket.get("horse_number", ""), bet_type)


def _normalize_combo_key(value: object, bet_type: str) -> str:
    numbers = _number_tokens(value)
    if not numbers:
        return ""
    if bet_type in ORDERED_BET_TYPES:
        return ">".join(numbers)
    if bet_type in UNORDERED_BET_TYPES:
        return "-".join(sorted(numbers, key=lambda item: int(item)))
    return numbers[0]


def _number_tokens(value: object) -> list[str]:
    if value in (None, ""):
        return []
    normalized = unicodedata.normalize("NFKC", str(value))
    return [str(int(token)) for token in re.findall(r"\d+", normalized)]


def _normalize_bet_type(value: object) -> str:
    key = _compact_label(value)
    return ALIAS_TO_BET_TYPE.get(key, "")


def _typed_payout_columns(bet_type: str) -> tuple[str, ...]:
    aliases = BET_TYPE_ALIASES.get(bet_type, (bet_type,))
    columns: list[str] = []
    for alias in aliases:
        normalized = str(alias)
        columns.extend(
            [
                f"{normalized}_payout",
                f"{normalized}_payout_per_100",
                f"{normalized}_払戻",
                f"{normalized}_払戻金",
            ]
        )
    return tuple(columns)


def _typed_combo_columns(bet_type: str) -> tuple[str, ...]:
    aliases = BET_TYPE_ALIASES.get(bet_type, (bet_type,))
    columns: list[str] = []
    for alias in aliases:
        normalized = str(alias)
        columns.extend(
            [
                f"{normalized}_numbers",
                f"{normalized}_combination",
                f"{normalized}_combo",
                f"{normalized}_result",
                f"{normalized}_組番",
                f"{normalized}_馬番",
            ]
        )
    if bet_type == "wakuren":
        columns.extend(["wakuren_frames", "枠連_枠番"])
    return tuple(columns)


def _first_nonempty(row: dict[str, str], columns: tuple[str, ...]) -> str:
    for column in columns:
        value = _get_case_insensitive(row, column)
        if str(value).strip():
            return str(value).strip()
    return ""


def _get_case_insensitive(row: dict[str, str], column: str) -> str:
    if column in row:
        return str(row.get(column, ""))
    target = _compact_label(column)
    for key, value in row.items():
        if _compact_label(key) == target:
            return str(value)
    return ""


def _bet_type_breakdown(by_type: dict[str, dict[str, float | int]]) -> dict[str, dict[str, float | int]]:
    breakdown: dict[str, dict[str, float | int]] = {}
    for bet_type in BET_TYPES:
        data = by_type.get(bet_type)
        if not data:
            continue
        invested = int(data.get("invested", 0))
        returned = float(data.get("returned", 0.0))
        ticket_count = int(data.get("ticket_count", 0))
        hit_ticket_count = int(data.get("hit_ticket_count", 0))
        breakdown[bet_type] = {
            "ticket_count": ticket_count,
            "hit_ticket_count": hit_ticket_count,
            "ticket_hit_rate": hit_ticket_count / max(ticket_count, 1),
            "invested": invested,
            "returned": returned,
            "roi": (returned / invested) if invested > 0 else 0.0,
        }
    return breakdown


def _label_status(payout_lookup: dict[tuple[str, str, str], float], ticket_bet_types: list[str]) -> str:
    if not payout_lookup:
        return "missing"
    available = {bet_type for _, bet_type, _ in payout_lookup}
    missing_ticket_types = [bet_type for bet_type in ticket_bet_types if bet_type not in available]
    return "partial" if missing_ticket_types else "available"


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    mdd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    return mdd


def _parse_files_changed(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _infer_results_path(input_path: Path) -> Path | None:
    direct = input_path.with_name("results.csv")
    if direct.exists():
        return direct
    candidate = ROOT / "tasks/horse_racing_ev/files/valid/results.csv"
    if candidate.exists():
        return candidate
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate EV strategy with labeled results when available.")
    parser.add_argument("--input", default="data/processed/race_last5.csv")
    parser.add_argument("--results", default="")
    parser.add_argument("--out", default="report/strategy_eval.json")
    parser.add_argument("--min-ev", type=float, default=1.05)
    parser.add_argument("--max-bets-per-race", type=int, default=2)
    parser.add_argument("--stake", type=int, default=100)
    parser.add_argument("--baseline-json", default="")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--hypothesis", default="")
    parser.add_argument("--files-changed", default="")
    parser.add_argument("--log-dir", default="experiments")
    args = parser.parse_args()

    input_path = Path(args.input)
    rows = load_rows(input_path)

    results_path = Path(args.results) if args.results else _infer_results_path(input_path)
    results = load_results(results_path) if results_path and results_path.exists() else None
    metrics = evaluate_strategy(
        rows=rows,
        min_ev=args.min_ev,
        max_bets_per_race=args.max_bets_per_race,
        stake_per_bet=args.stake,
        results=results,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.baseline_json:
        before = json.loads(Path(args.baseline_json).read_text(encoding="utf-8"))
        decision, reason = decide_keep_or_revert(before, metrics)
        comparison = {
            "experiment_id": args.experiment_id or datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S"),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit_before": before.get("git_commit", "unknown"),
            "git_commit_after": metrics.get("git_commit", "unknown"),
            "hypothesis": args.hypothesis,
            "files_changed": _parse_files_changed(args.files_changed),
            "before": before,
            "after": metrics,
            "decision": decision,
            "reason": reason,
            "results_path": str(results_path) if results_path else "",
        }
        log_dir = Path(args.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{comparison['experiment_id']}.json"
        log_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")

        payload = {"metrics": metrics, "decision": decision, "reason": reason, "log": str(log_path)}
        print(json.dumps(payload, ensure_ascii=False))
        return

    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()
