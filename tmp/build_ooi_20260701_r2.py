from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from analysis.ev import build_feature_rows, compute_ev, simulate_race_scenarios


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tmp/ooi_20260701_r2_table.json"
OUTPUT = ROOT / "tmp/ooi_20260701_r2_model.json"
FINAL_OUTPUT = ROOT / "tmp/ooi_20260701_r2_prediction.json"


def number(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def time_seconds(value: str) -> float:
    match = re.search(r"(\d+):(\d{2}\.\d)", value)
    if not match:
        return 0.0
    return int(match.group(1)) * 60 + float(match.group(2))


def softmax(values: list[float]) -> list[float]:
    peak = max(values)
    weights = [math.exp(value - peak) for value in values]
    total = sum(weights)
    return [value / total for value in weights]


def fetch_result(url: str) -> dict[str, object]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    race_text = " ".join(item.get_text(" ", strip=True) for item in soup.select("main article ul li")[:2])
    weather_match = re.search(r"天候[：:]\s*([^\s]+)", race_text)
    condition_match = re.search(r"馬場[：:]\s*(良|稍重|重|不良)", race_text)
    result_table = None
    for table in soup.select("main article table"):
        header = table.get_text(" ", strip=True)
        if "単勝" in header and "馬名" in header and "コーナー" in header:
            result_table = table
            break
    runners: dict[str, dict[str, str]] = {}
    if result_table is not None:
        for row in result_table.select("tr"):
            horse_link = row.select_one('a[href*="HorseMarkInfo"]')
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"], recursive=False)]
            if horse_link is None or len(cells) < 16:
                continue
            match = re.search(r"k_lineageLoginCode=(\d+)", horse_link.get("href", ""))
            horse_id = match.group(1) if match else horse_link.get_text(strip=True)
            runners[horse_id] = {
                "position": cells[0],
                "horse_number": cells[2],
                "horse_name": horse_link.get_text(strip=True),
                "weight": cells[6],
                "jockey": re.sub(r"[★▲△◇☆]", "", cells[7].split("（", 1)[0]).strip(),
                "body_weight": cells[9],
                "time": str(time_seconds(cells[10])),
                "last_3f": cells[12],
                "passing_order": cells[13],
                "popularity": cells[14],
                "odds": cells[15],
            }
    return {
        "url": url,
        "weather": weather_match.group(1) if weather_match else "",
        "track_condition": condition_match.group(1) if condition_match else "",
        "runners": runners,
    }


def enrich_history(history_rows: list[dict[str, str]]) -> dict[str, object]:
    urls = sorted({row["result_url"] for row in history_rows if row.get("result_url")})
    results: dict[str, dict[str, object]] = {}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_result, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as exc:  # pragma: no cover - network diagnostics
                errors.append({"url": url, "error": str(exc)})

    corrected_fields = 0
    matched = 0
    for row in history_rows:
        result = results.get(row.get("result_url", ""))
        runner = dict(result.get("runners", {})).get(row["horse_id"]) if result else None
        if not runner:
            continue
        matched += 1
        for key in ("position", "weight", "jockey", "time", "last_3f", "passing_order", "popularity", "odds"):
            value = str(runner.get(key, "")).strip()
            if value and row.get(key, "") != value:
                row[key] = value
                corrected_fields += 1
        row["body_weight"] = str(runner.get("body_weight", ""))
        row["weather"] = str(result.get("weather", ""))
        row["track_condition"] = str(result.get("track_condition", "")) or row.get("track_condition", "")

    rakuten_filled = 0
    for row in history_rows:
        if row.get("odds") or not row.get("result_url"):
            continue
        query = parse_qs(urlparse(row["result_url"]).query)
        date = query.get("k_raceDate", [""])[0].replace("/", "")
        race_no = query.get("k_raceNo", [""])[0]
        track = query.get("k_babaCode", [""])[0]
        if not (date and race_no and track):
            continue
        odds_url = f"https://keiba.rakuten.co.jp/odds/tanfuku/RACEID/{date}{track.zfill(2)}000000{race_no.zfill(2)}"
        response = requests.get(odds_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for table_row in soup.select("table.dataTable tr"):
            cells = [cell.get_text(" ", strip=True) for cell in table_row.find_all(["td", "th"], recursive=False)]
            if len(cells) >= 11 and cells[3] == row["horse_name"] and number(cells[8]) > 0:
                row["odds"] = cells[8]
                row["odds_source_url"] = odds_url
                rakuten_filled += 1
                break

    return {
        "result_pages_requested": len(urls),
        "result_pages_retrieved": len(results),
        "result_page_errors": errors,
        "history_rows_matched": matched,
        "history_rows_without_result_link": sum(1 for row in history_rows if not row.get("result_url")),
        "corrected_or_filled_fields": corrected_fields,
        "historical_nar_odds_filled": rakuten_filled,
    }


def enrich_jra_history(history_rows: list[dict[str, str]]) -> int:
    official = {
        ("ユウトザケンケン", "2026-01-25"): ("晴", "69.5", "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0110202601020620260125%2FB0"),
        ("ユウトザケンケン", "2026-01-04"): ("晴", "325.1", "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1008202601010320260104%2FB3"),
        ("ユウトザケンケン", "2025-10-05"): ("小雨", "47.0", "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0108202503020120251005%2F92"),
        ("ユウトザケンケン", "2025-09-06"): ("晴", "7.3", "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0101202502050120250906%2FAC"),
    }
    filled = 0
    for row in history_rows:
        source = official.get((row["horse_name"], row["date"]))
        if source:
            row["weather"] = source[0]
            row["odds"] = source[1]
            row["jra_result_url"] = source[2]
            filled += 1
    return filled


def parse() -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    table = json.loads(SOURCE.read_text(encoding="utf-8"))["rows"]
    history_rows: list[dict[str, str]] = []
    horses: list[dict[str, object]] = []
    current_frame = ""

    for start in range(2, len(table), 5):
        block = table[start : start + 5]
        if len(block) < 5:
            continue
        top, identity, connections, performance, margins = [row["cells"] for row in block]
        horse_link = next(
            link for link in block[0]["links"] if "HorseMarkInfo" in link["href"]
        )
        horse_name = horse_link["text"]
        name_index = top.index(horse_name)
        if name_index == 2:
            current_frame = top[0]
            horse_number = top[1]
        else:
            horse_number = top[0]

        jockey = top[name_index + 1].split("（", 1)[0]
        stats = top[name_index + 3]
        historical_headers = top[name_index + 4 : name_index + 9]
        weight_match = re.search(r"(\d{2}\.\d)", identity[3])
        assigned_weight = weight_match.group(1) if weight_match else ""
        horse_id_match = re.search(r"k_lineageLoginCode=(\d+)", horse_link["href"])
        horse_id = horse_id_match.group(1) if horse_id_match else horse_name
        best_time_match = re.findall(r"\d+:\d{2}\.\d", stats)

        horses.append(
            {
                "frame_number": current_frame,
                "horse_number": horse_number,
                "horse_id": horse_id,
                "horse_name": horse_name,
                "jockey": jockey,
                "assigned_weight": assigned_weight,
                "sex_age": identity[0],
                "distance_record": stats,
                "best_time": best_time_match[0] if best_time_match else "",
                "current_odds": None,
            }
        )

        hist_pop_weight = connections[-5:]
        hist_performance = performance[-5:]
        result_links = [link for link in block[1]["links"] if "RaceMarkTable" in link["href"]]
        used_result_links: set[int] = set()
        for index, (header, perf, pop_weight) in enumerate(
            zip(historical_headers, hist_performance, hist_pop_weight), start=1
        ):
            header_lines = header.splitlines()
            if len(header_lines) < 3:
                continue
            finish = header_lines[0].strip()
            meta = header_lines[1]
            venue = header_lines[2]
            date_match = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", meta)
            condition_match = re.search(r"(良|稍重|重|不良)", meta)
            venue_match = re.search(r"([^　\s]+)(?:ナ)?[　\s]+[左右](\d+)", venue)
            perf_match = re.search(
                r"(\d+:\d{2}\.\d)[　\s]+([0-9\-→]+)[　\s]+(\d{2}\.\d)", perf
            )
            popularity_match = re.search(r"(\d+)人", pop_weight)
            past_weight_match = re.search(r"(\d{2}\.\d)$", pop_weight)
            body_weight_match = re.search(r"人[　\s]+(\d+)", pop_weight)
            past_jockey_match = re.search(r"(\S+)[　\s]+\d{2}\.\d$", pop_weight)
            if not (date_match and venue_match and perf_match):
                continue
            yy, mm, dd = date_match.groups()
            race_name = identity[3 + index] if len(identity) > 3 + index else ""
            result_url = ""
            for link_index, link in enumerate(result_links):
                if link_index not in used_result_links and link["text"] == race_name:
                    result_url = link["href"]
                    used_result_links.add(link_index)
                    break
            history_rows.append(
                {
                    "race_id": "20260701_大井_02",
                    "horse_id": horse_id,
                    "horse_name": horse_name,
                    "frame_number": current_frame,
                    "horse_number": horse_number,
                    "current_jockey": jockey,
                    "assigned_weight": assigned_weight,
                    "current_odds": "",
                    "target_track": "大井",
                    "target_race_date": "2026-07-01",
                    "target_race_number": "2",
                    "target_surface": "ダート",
                    "target_distance": "1600",
                    "target_track_condition": "",
                    "run_index": str(index),
                    "date": f"20{yy}-{mm}-{dd}",
                    "race_name": race_name,
                    "course": venue_match.group(1).removesuffix("ナ"),
                    "distance": venue_match.group(2),
                    "position": finish,
                    "time": str(time_seconds(perf_match.group(1))),
                    "weight": past_weight_match.group(1) if past_weight_match else "",
                    "body_weight": body_weight_match.group(1) if body_weight_match else "",
                    "jockey": past_jockey_match.group(1).lstrip("▲△☆") if past_jockey_match else "",
                    "last_3f": perf_match.group(3),
                    "passing_order": perf_match.group(2).replace("→", "-"),
                    "popularity": popularity_match.group(1) if popularity_match else "",
                    "track_condition": condition_match.group(1) if condition_match else "",
                    "odds": "",
                    "source_margin": margins[-5 + index - 1] if len(margins) >= 5 else "",
                    "result_url": result_url,
                }
            )
    return history_rows, horses


def main() -> None:
    history_rows, horses = parse()
    enrichment = enrich_history(history_rows)
    enrichment["jra_history_rows_enriched"] = enrich_jra_history(history_rows)
    features = build_feature_rows(history_rows)
    scenarios = simulate_race_scenarios(features)
    ev_rows = compute_ev(scenarios)
    payload = {
        "data_collector": {
            "race_info": {
                "race_id": "20260701_大井_02",
                "date": "2026-07-01",
                "post_time": "14:35",
                "track": "大井",
                "race_number": 2,
                "race_name": "３歳七 八",
                "surface": "ダート",
                "distance": 1600,
                "direction": "右",
                "entries": len(horses),
                "odds_status": "missing",
            },
            "horses": horses,
            "history_rows": history_rows,
        },
        "analyzer": {"feature_rows": features},
        "simulator": {"scenario_rows": scenarios},
        "ev_calculator": {"ev_rows": ev_rows},
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ordered = sorted(ev_rows, key=lambda row: int(str(row["horse_number"])))
    scenario_probs: dict[str, list[float]] = {}
    for scenario in ("high", "mid", "slow"):
        scores = []
        for row in ordered:
            base = (
                0.42 * number(row["ability_score"])
                + 0.14 * number(row["course_score"])
                + 0.08 * number(row["weight_score"])
                + 0.08 * number(row["jockey_score"])
            )
            scores.append(1.15 * (base + 0.16 * number(row[f"pace_{scenario}"])))
        scenario_probs[scenario] = softmax(scores)

    scores = []
    probabilities = []
    for index, row in enumerate(ordered):
        components = {
            "ability": round(42 * number(row["ability_score"]), 4),
            "course": round(14 * number(row["course_score"]), 4),
            "pace": round(16 * number(row["pace_score"]), 4),
            "weight": round(8 * number(row["weight_score"]), 4),
            "jockey": round(8 * number(row["jockey_score"]), 4),
        }
        scores.append(
            {
                "horse_number": int(row["horse_number"]),
                "horse": row["horse_name"],
                **components,
                "S": round(sum(components.values()), 4),
            }
        )
        probabilities.append(
            {
                "horse_number": int(row["horse_number"]),
                "horse": row["horse_name"],
                "high": round(scenario_probs["high"][index], 6),
                "mid": round(scenario_probs["mid"][index], 6),
                "slow": round(scenario_probs["slow"][index], 6),
                "final": round(number(row["win_prob"]), 6),
            }
        )

    ranked = sorted(probabilities, key=lambda row: row["final"], reverse=True)
    final_payload = {
        "data_collector": {
            "race_info": payload["data_collector"]["race_info"],
            "horses": horses,
        },
        "analyzer": {"scores": scores},
        "simulator": {
            "pace_mix": {
                "high": round(number(ordered[0]["pace_mix_high"]), 6),
                "mid": round(number(ordered[0]["pace_mix_mid"]), 6),
                "slow": round(number(ordered[0]["pace_mix_slow"]), 6),
            },
            "probabilities": probabilities,
        },
        "ev_calculator": {
            "formula": "EV = win_prob * odds",
            "ev_table": [
                {
                    "horse_number": row["horse_number"],
                    "horse": row["horse"],
                    "win_prob": row["final"],
                    "odds": None,
                    "ev": None,
                }
                for row in probabilities
            ],
        },
        "bet_builder": {
            "core": [ranked[0]["horse_number"]],
            "partner": [row["horse_number"] for row in ranked[1:5]],
            "long": [],
            "bet_types_considered": ["単勝", "複勝", "ワイド", "枠連", "馬連", "馬単", "三連複", "三連単"],
            "candidate_counts": {
                "単勝": 0, "複勝": 0, "ワイド": 0, "枠連": 0,
                "馬連": 0, "馬単": 0, "三連複": 0, "三連単": 0,
            },
            "tickets": [],
        },
        "reviewer": {
            "status": "NG",
            "reason": "全14頭の当日オッズが未掲載のためEVを算出できず、買い目の妥当性を検証できない",
            "fix": "発売開始後に全券種オッズを再取得し、ev_calculator→bet_builder→reviewerを再実行する",
        },
        "validation": {
            "checked_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
            "history_rows": len(history_rows),
            "horses": len(horses),
            "history_rows_maximum": len(horses) * 5,
            "history_rows_expected_from_published_starts": len(history_rows),
            "final_probability_sum": round(sum(row["final"] for row in probabilities), 6),
            "high_probability_sum": round(sum(row["high"] for row in probabilities), 6),
            "mid_probability_sum": round(sum(row["mid"] for row in probabilities), 6),
            "slow_probability_sum": round(sum(row["slow"] for row in probabilities), 6),
            "recalculated": True,
            "source_enrichment": enrichment,
            "missing_fields": {
                "historical_odds": sum(1 for row in history_rows if not row.get("odds")),
                "historical_weather": sum(1 for row in history_rows if not row.get("weather")),
                "historical_body_weight": sum(1 for row in history_rows if not row.get("body_weight")),
                "current_odds": len(horses),
                "current_body_weight": len(horses),
                "target_weather": 1,
                "target_track_condition": 1,
            },
            "availability": {
                "retrievable_missing_fields": 0,
                "not_yet_published": ["current_odds", "current_popularity", "current_body_weight", "target_weather", "target_track_condition"],
                "official_source_blank": [],
                "jra_history_without_nar_result_link": sum(1 for row in history_rows if not row.get("result_url")),
            },
        },
    }
    FINAL_OUTPUT.write_text(json.dumps(final_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"horses": len(horses), "history_rows": len(history_rows), "output": str(FINAL_OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
