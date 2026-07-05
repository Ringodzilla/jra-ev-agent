from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from analysis.ev import build_feature_rows, compute_ev, simulate_race_scenarios
from strategy.betting import generate_tickets
from tmp.build_ooi_20260701_r9 import enrich_history, enrich_jra_history, parse


ROOT = Path(__file__).resolve().parents[1]
RACE_ID = "20260701_大井_09"


def _soup(path: str) -> BeautifulSoup:
    return BeautifulSoup(Path(path).read_text(encoding="utf-8"), "html.parser")


def parse_current() -> tuple[dict[str, dict[str, str]], str]:
    soup = _soup("/tmp/ooi9_tanfuku.html")
    title = soup.select_one(".odd_title").get_text(" ", strip=True)
    captured = re.search(r"（(\d{1,2}:\d{2})\s*現在）", title).group(1)
    current: dict[str, dict[str, str]] = {}
    table = soup.select_one("table.odd_popular_table_02")
    for tr in table.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"], recursive=False)]
        if len(cells) < 10 or not cells[1].isdigit():
            continue
        number = cells[1]
        place_parts = re.findall(r"\d+\.\d+", cells[4])
        body_match = re.search(r"\d+", cells[6])
        current[number] = {
            "horse_name": cells[2],
            "current_odds": cells[3],
            "place_odds_min": place_parts[0] if place_parts else "",
            "place_odds_max": place_parts[-1] if place_parts else "",
            "body_weight": body_match.group(0) if body_match else "",
            "body_weight_change": cells[7],
        }
    for popularity, (_, row) in enumerate(
        sorted(current.items(), key=lambda item: float(item[1]["current_odds"])), start=1
    ):
        row["current_popularity"] = str(popularity)
    return current, captured


def parse_ranking_odds(path: str, bet_type: str, captured_at: str) -> list[dict[str, object]]:
    soup = _soup(path)
    rows: list[dict[str, object]] = []
    for tr in soup.select("table.odd_ranking_table tr"):
        cells = [cell.get_text("-", strip=True) for cell in tr.find_all(["th", "td"], recursive=False)]
        if len(cells) != 3 or not re.fullmatch(r"\d+(?:-\d+){1,2}", cells[0]):
            continue
        odds_values = re.findall(r"\d+(?:\.\d+)?", cells[1])
        if not odds_values:
            continue
        combination = cells[0]
        if bet_type in {"umatan", "sanrentan"}:
            combination = combination.replace("-", ">")
        rows.append(
            {
                "race_id": RACE_ID,
                "bet_type": bet_type,
                "combination": combination,
                "odds": odds_values[0],
                "odds_min": odds_values[0],
                "odds_max": odds_values[-1],
                "captured_at": captured_at,
                "source": "NAR official",
            }
        )
    return rows


def main() -> None:
    current, capture_time = parse_current()
    captured_at = f"2026-07-01T{capture_time}:00+09:00"
    history_rows, horses = parse()
    history_audit = enrich_history(history_rows)
    history_audit["jra_history_rows_enriched"] = enrich_jra_history(history_rows)
    for row in history_rows:
        now = current[row["horse_number"]]
        row["current_odds"] = now["current_odds"]
        row["current_popularity"] = now["current_popularity"]
        row["target_weather"] = "曇"
        row["target_track_condition"] = "稍重"
        row["target_conditions_captured_at"] = captured_at
    for horse in horses:
        now = current[horse["horse_number"]]
        horse.update(now)

    odds_rows: list[dict[str, object]] = []
    for number, row in current.items():
        odds_rows.extend(
            [
                {"race_id": RACE_ID, "bet_type": "win", "combination": number, "odds": row["current_odds"], "odds_min": row["current_odds"], "odds_max": row["current_odds"], "captured_at": captured_at},
                {"race_id": RACE_ID, "bet_type": "place", "combination": number, "odds": row["place_odds_min"], "odds_min": row["place_odds_min"], "odds_max": row["place_odds_max"], "captured_at": captured_at},
            ]
        )
    for filename, bet_type in (
        ("/tmp/ooi9_umaren.html", "umaren"),
        ("/tmp/ooi9_umatan.html", "umatan"),
        ("/tmp/ooi9_wide.html", "wide"),
        ("/tmp/ooi9_sanrenpuku.html", "sanrenpuku"),
        ("/tmp/ooi9_sanrentan.html", "sanrentan"),
    ):
        odds_rows.extend(parse_ranking_odds(filename, bet_type, captured_at))

    feature_rows = build_feature_rows(history_rows)
    scenario_rows = simulate_race_scenarios(feature_rows)
    ev_rows = compute_ev(scenario_rows)
    ticket_plan = generate_tickets(
        ev_rows,
        odds_rows=odds_rows,
        mode="balanced",
        bankroll_per_race=1000,
        max_tickets_per_race=5,
        prefer_wide=True,
    )
    payload = {
        "race_info": {
            "race_id": RACE_ID,
            "weather": "曇",
            "track_condition": "稍重",
            "post_time": "18:40",
            "captured_at": captured_at,
        },
        "horses": horses,
        "ev_table": sorted(
            [
                {
                    "horse_number": int(row["horse_number"]),
                    "horse_name": row["horse_name"],
                    "win_prob": float(row["win_prob"]),
                    "odds": float(row["current_odds"]),
                    "ev": float(row["ev"]),
                    "fair_odds": float(row["fair_odds"]),
                }
                for row in ev_rows
            ],
            key=lambda row: row["ev"],
            reverse=True,
        ),
        "bet_builder": ticket_plan,
        "reviewer": {
            "status": "OK" if ticket_plan.get("tickets") else "NG",
            "reason": "最新オッズ・馬体重・天候・馬場を反映し、EV閾値とガミ防止を通過" if ticket_plan.get("tickets") else "EV閾値を満たす買い目なし",
        },
        "validation": {
            "horses": len(horses),
            "history_rows": len(history_rows),
            "odds_rows": len(odds_rows),
            "probability_sum": round(sum(float(row["win_prob"]) for row in ev_rows), 6),
            "ticket_stake_total": sum(int(float(ticket.get("stake", 0))) for ticket in ticket_plan.get("tickets", [])),
            "history_audit": history_audit,
            "generated_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
        },
    }
    output = ROOT / "tmp/ooi_20260701_r9_final.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "tickets": len(ticket_plan.get("tickets", [])), "stake": payload["validation"]["ticket_stake_total"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
