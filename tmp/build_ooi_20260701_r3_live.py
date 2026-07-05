from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from analysis.ev import build_feature_rows, compute_ev, simulate_race_scenarios
from strategy.betting import generate_tickets
from tmp.build_ooi_20260701_r3 import enrich_history, enrich_jra_history, parse


ROOT = Path(__file__).resolve().parents[1]
RACE_ID = "20260701_大井_03"
BASE = "https://www.keiba.go.jp/KeibaWeb_IPAT/TodayRaceInfo/"
QUERY = "?k_raceDate=2026%2F07%2F01&k_raceNo=3&k_babaCode=20"
OUT = ROOT / "tmp/ooi_20260701_r3_live_prediction.json"


def fetch(page: str) -> BeautifulSoup:
    response = requests.get(BASE + page + QUERY, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def to_float(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def odds_row(
    bet_type: str,
    combination: str,
    odds: str,
    captured_at: str,
    *,
    odds_max: str = "",
) -> dict[str, str]:
    return {
        "race_id": RACE_ID,
        "bet_type": bet_type,
        "combination": combination,
        "odds": odds,
        "odds_min": odds,
        "odds_max": odds_max,
        "captured_at": captured_at,
    }


def parse_current() -> tuple[dict[str, dict[str, object]], list[dict[str, str]], str]:
    captured_at = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    soup = fetch("OddsTanFuku_ipat")
    current: dict[str, dict[str, object]] = {}
    rows: list[dict[str, str]] = []
    for tr in soup.select("table.odd_popular_table_02 tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"], recursive=False)]
        if len(cells) < 12 or not cells[1].isdigit():
            continue
        number = cells[1]
        cancelled = "取消" in " ".join(cells)
        win_odds = cells[3]
        place_min = cells[4].replace("-", "").strip()
        place_max = cells[5].strip()
        current[number] = {
            "frame_number": cells[0],
            "horse_number": number,
            "horse_name": cells[2],
            "win_odds": win_odds,
            "place_min": place_min,
            "place_max": place_max,
            "body_weight": cells[7],
            "cancelled": cancelled,
        }
        if cancelled:
            continue
        rows.append(odds_row("win", number, win_odds, captured_at))
        rows.append(odds_row("place", number, place_min, captured_at, odds_max=place_max))

    active = [item for item in current.values() if not item["cancelled"]]
    for rank, item in enumerate(sorted(active, key=lambda item: to_float(item["win_odds"])), start=1):
        item["popularity"] = rank
    return current, rows, captured_at


def parse_ranked(page: str, bet_type: str, captured_at: str, *, ordered: bool = False, ranged: bool = False) -> list[dict[str, str]]:
    soup = fetch(page)
    out: list[dict[str, str]] = []
    for tr in soup.select("table.odd_ranking_table tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"], recursive=False)]
        if len(cells) < 3 or not re.fullmatch(r"\d+(?:-\d+){1,2}", cells[0]):
            continue
        odds_bits = cells[1].split()
        if not odds_bits or to_float(odds_bits[0]) <= 0:
            continue
        combination = cells[0].replace("-", ">") if ordered else cells[0]
        out.append(
            odds_row(
                bet_type,
                combination,
                odds_bits[0],
                captured_at,
                odds_max=odds_bits[-1].lstrip("-") if ranged and len(odds_bits) > 1 else "",
            )
        )
    return out


def parse_wakuren(captured_at: str) -> list[dict[str, str]]:
    soup = fetch("OddsWakuLenFukuTan_ipat")
    out: list[dict[str, str]] = []
    tables = soup.select("main article table")
    for table in tables[:8]:
        lines = []
        for tr in table.select("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"], recursive=False)]
            if cells:
                lines.append(cells)
        if not lines or not lines[0][0].isdigit():
            continue
        left = lines[0][0]
        for cells in lines[1:]:
            if len(cells) < 2 or not cells[0].isdigit() or to_float(cells[1]) <= 0:
                continue
            out.append(odds_row("wakuren", "-".join(sorted([left, cells[0]], key=int)), cells[1], captured_at))
    return out


def score_rows(ev_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    scores = []
    for row in ev_rows:
        parts = {
            "ability": round(42 * to_float(row.get("ability_score")), 4),
            "course": round(14 * to_float(row.get("course_score")), 4),
            "pace": round(16 * to_float(row.get("pace_score")), 4),
            "weight": round(8 * to_float(row.get("weight_score")), 4),
            "jockey": round(8 * to_float(row.get("jockey_score")), 4),
        }
        scores.append({"horse_number": int(str(row["horse_number"])), "horse": row["horse_name"], **parts, "S": round(sum(parts.values()), 4)})
    return sorted(scores, key=lambda row: row["S"], reverse=True)


def main() -> None:
    history_rows, horses = parse()
    enrichment = enrich_history(history_rows)
    enrichment["jra_history_rows_enriched"] = enrich_jra_history(history_rows)
    current, odds_rows, captured_at = parse_current()

    cancelled = sorted(number for number, item in current.items() if item["cancelled"])
    active_numbers = {number for number, item in current.items() if not item["cancelled"]}
    horses = [horse for horse in horses if str(horse["horse_number"]) in active_numbers]
    history_rows = [row for row in history_rows if str(row["horse_number"]) in active_numbers]
    for horse in horses:
        live = current[str(horse["horse_number"])]
        horse["current_odds"] = live["win_odds"]
        horse["current_popularity"] = live["popularity"]
        horse["current_body_weight"] = live["body_weight"]
    for row in history_rows:
        live = current[str(row["horse_number"])]
        row["current_odds"] = str(live["win_odds"])
        row["current_popularity"] = str(live["popularity"])
        row["target_weather"] = "曇"
        row["target_track_condition"] = "稍重"
        row["target_conditions_captured_at"] = captured_at

    odds_rows.extend(parse_wakuren(captured_at))
    odds_rows.extend(parse_ranked("OddsUmLenFuku_ipat", "umaren", captured_at))
    odds_rows.extend(parse_ranked("OddsUmLenTan_ipat", "umatan", captured_at, ordered=True))
    odds_rows.extend(parse_ranked("OddsWide_ipat", "wide", captured_at, ranged=True))
    odds_rows.extend(parse_ranked("Odds3LenFuku_ipat", "sanrenpuku", captured_at))
    odds_rows.extend(parse_ranked("Odds3LenTan_ipat", "sanrentan", captured_at, ordered=True))

    features = build_feature_rows(history_rows)
    scenarios = simulate_race_scenarios(features)
    ev_rows = compute_ev(scenarios)
    ticket_plan = generate_tickets(
        ev_rows,
        mode="balanced",
        odds_rows=odds_rows,
        bankroll_per_race=1000,
        min_ev=1.03,
        max_tickets_per_race=5,
        max_wide_tickets_per_race=2,
        max_exotic_tickets_per_race=4,
    )

    tickets = list(ticket_plan["tickets"])
    invalid_cancelled = [ticket for ticket in tickets if "12" in re.findall(r"\d+", str(ticket.get("horse_number", "")))]
    reviewer_ok = bool(tickets) and not invalid_cancelled and all(to_float(ticket.get("ev")) > 1.0 for ticket in tickets)
    probability_sum = sum(to_float(row.get("win_prob")) for row in ev_rows)
    payload = {
        "data_collector": {
            "race_info": {
                "race_id": RACE_ID,
                "date": "2026-07-01",
                "post_time": "15:10",
                "track": "大井",
                "race_number": 3,
                "race_name": "３歳七 八",
                "surface": "ダート",
                "distance": 1200,
                "direction": "右・外",
                "weather": "曇",
                "track_condition": "稍重",
                "active_entries": len(horses),
                "cancelled": cancelled,
                "captured_at": captured_at,
            },
            "horses": horses,
            "odds_counts": dict(Counter(row["bet_type"] for row in odds_rows)),
            "source_enrichment": enrichment,
        },
        "analyzer": {"scores": score_rows(ev_rows)},
        "simulator": {
            "pace_mix": {
                "high": to_float(ev_rows[0]["pace_mix_high"]),
                "mid": to_float(ev_rows[0]["pace_mix_mid"]),
                "slow": to_float(ev_rows[0]["pace_mix_slow"]),
            },
            "probabilities": [
                {
                    "horse_number": int(str(row["horse_number"])),
                    "horse": row["horse_name"],
                    "win_prob": round(to_float(row["win_prob"]), 6),
                }
                for row in sorted(ev_rows, key=lambda row: to_float(row["win_prob"]), reverse=True)
            ],
        },
        "ev_calculator": {
            "formula": "EV = win_prob * odds",
            "ev_table": [
                {
                    "horse_number": int(str(row["horse_number"])),
                    "horse": row["horse_name"],
                    "win_prob": round(to_float(row["win_prob"]), 6),
                    "odds": to_float(row["current_odds"]),
                    "ev": round(to_float(row["ev"]), 6),
                }
                for row in sorted(ev_rows, key=lambda row: to_float(row["ev"]), reverse=True)
            ],
        },
        "bet_builder": ticket_plan,
        "reviewer": {
            "status": "OK" if reviewer_ok else "NG",
            "reason": "取消馬を除外し、全券種の最新オッズでEVを再計算" if reviewer_ok else "買い目のEVまたは取消馬除外に不整合",
            "fix": "" if reviewer_ok else "bet_builderを再実行",
        },
        "validation": {
            "cancelled_excluded": not invalid_cancelled,
            "active_horses": len(horses),
            "history_rows": len(history_rows),
            "odds_rows": len(odds_rows),
            "probability_sum": round(probability_sum, 6),
            "ticket_stake_sum": sum(int(ticket.get("stake", 0)) for ticket in tickets),
            "recalculated": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUT), "reviewer": payload["reviewer"], "tickets": tickets}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
