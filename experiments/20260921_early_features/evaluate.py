"""Read-only, time-split audit of candidate early features on frozen JRA predictions."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BETAS = tuple(step / 10 for step in range(-6, 7))
TRAIN_THROUGH = "2026-09-06"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _labels() -> dict[str, dict]:
    labels = {}
    with (ROOT / "data/processed/result_labels.csv").open(encoding="utf-8") as file_obj:
        for row in csv.DictReader(file_obj):
            if row["式別"] == "単勝":
                labels[row["race_id"]] = {
                    "winner_number": int(row["馬番"]),
                    "result_source": "data/processed/result_labels.csv",
                }
    labels.update(_read_json(HERE / "official_result_labels.json"))
    return labels


def _latest_frozen_dir(race_id: str) -> Path | None:
    candidates = []
    for path in (ROOT / "report/final_predictions" / race_id).glob("*/final_decision.json"):
        folder = path.parent
        required = [folder / name for name in (
            "01_data_collector.json", "03_simulator.json", "04_ev_calculator.json"
        )]
        if not all(item.exists() for item in required):
            continue
        decision = _read_json(path)
        issued = datetime.fromisoformat(decision["issued_at"])
        post = datetime.fromisoformat(decision["post_time"])
        if issued >= post:
            continue
        candidates.append((issued, folder))
    return max(candidates)[1] if candidates else None


def _history_date(text: str) -> date | None:
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not match:
        return None
    return date(*map(int, match.groups()))


def _standardize(values: dict[int, float | None]) -> dict[int, float]:
    valid = [value for value in values.values() if value is not None]
    if not valid:
        return {key: 0.0 for key in values}
    mean = sum(valid) / len(valid)
    filled = {key: mean if value is None else value for key, value in values.items()}
    variance = sum((value - mean) ** 2 for value in filled.values()) / len(filled)
    sd = math.sqrt(variance)
    return {key: (value - mean) / sd if sd > 0 else 0.0 for key, value in filled.items()}


def _race(race_id: str, label: dict) -> dict | None:
    folder = _latest_frozen_dir(race_id)
    if folder is None:
        return None
    ev_path = folder / "04_ev_calculator.json"
    collector_path = folder / "01_data_collector.json"
    ev_rows = _read_json(ev_path).get("ev_rows", [])
    history = _read_json(collector_path).get("rows", [])
    if not ev_rows:
        return None

    race_date = date.fromisoformat(race_id[:4] + "-" + race_id[4:6] + "-" + race_id[6:8])
    numbers = [int(row["horse_number"]) for row in ev_rows]
    winner = label["winner_number"]
    if len(numbers) != len(set(numbers)) or winner not in numbers:
        raise ValueError(f"bad frozen lineup or winner: {race_id}")
    baseline = {int(row["horse_number"]): float(row["win_prob"]) for row in ev_rows}
    if abs(sum(baseline.values()) - 1) > 0.001:
        raise ValueError(f"probabilities do not sum to one: {race_id}")

    by_horse = defaultdict(list)
    history_dates = defaultdict(list)
    for row in history:
        number = int(row["horse_number"])
        previous_date = _history_date(str(row.get("date", "")))
        if previous_date is None and str(row.get("neutral_history_fallback", "")).lower() == "true":
            continue
        if previous_date is None or previous_date >= race_date:
            raise ValueError(f"history is not strictly before race: {race_id}")
        history_dates[number].append(previous_date)
        if row.get("track_condition") != row.get("target_track_condition"):
            continue
        try:
            corner = int(row["passing_order"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= corner <= 20:
            by_horse[number].append(1.0 if corner <= 4 else 0.0)
    rest_days = {
        number: (race_date - max(history_dates[number])).days if history_dates[number] else None
        for number in numbers
    }
    rest_values = {
        number: math.log1p(days) if days is not None else None
        for number, days in rest_days.items()
    }
    stale_model_rest_values = sum(
        rest_days[int(row["horse_number"])] is not None
        and int(float(row.get("days_since_last_run") or 0)) != rest_days[int(row["horse_number"])]
        for row in ev_rows
    )
    ground_values = {
        number: sum(by_horse[number]) / len(by_horse[number]) if by_horse[number] else None
        for number in numbers
    }

    return {
        "race_id": race_id,
        "race_date": race_date.isoformat(),
        "winner_number": winner,
        "snapshot_dir": str(folder.relative_to(ROOT)),
        "ev_sha256": hashlib.sha256(ev_path.read_bytes()).hexdigest(),
        "collector_sha256": hashlib.sha256(collector_path.read_bytes()).hexdigest(),
        "result_source": label.get("result_url") or label.get("result_source", ""),
        "baseline": baseline,
        "features": {
            "rest_interval": _standardize(rest_values),
            "same_ground_front4": _standardize(ground_values),
        },
        "coverage": {
            "rest_known": sum(value is not None for value in rest_values.values()),
            "model_rest_value_differs_from_history": stale_model_rest_values,
            "same_ground_corner_known": sum(value is not None for value in ground_values.values()),
            "runner_count": len(numbers),
            "same_ground_history_rows": sum(map(len, by_horse.values())),
        },
    }


def _tilt(race: dict, feature: str, beta: float) -> dict[int, float]:
    weights = {
        number: probability * math.exp(beta * race["features"][feature][number])
        for number, probability in race["baseline"].items()
    }
    total = sum(weights.values())
    return {number: value / total for number, value in weights.items()}


def _metrics(races: list[dict], feature: str | None, beta: float = 0.0) -> dict:
    log_losses = []
    brier_sums = []
    top1_hits = 0
    by_race = []
    for race in races:
        probabilities = _tilt(race, feature, beta) if feature else race["baseline"]
        winner = race["winner_number"]
        win_prob = probabilities[winner]
        loss = -math.log(max(win_prob, 1e-12))
        brier = sum((probability - (number == winner)) ** 2 for number, probability in probabilities.items())
        rank = 1 + sum(value > win_prob for value in probabilities.values())
        log_losses.append(loss)
        brier_sums.append(brier)
        top1_hits += rank == 1
        by_race.append({
            "race_id": race["race_id"], "winner_probability": win_prob,
            "winner_rank": rank, "log_loss": loss, "brier_sum": brier,
        })
    return {
        "race_count": len(races),
        "mean_log_loss": sum(log_losses) / len(races),
        "mean_brier_sum": sum(brier_sums) / len(races),
        "top1_hits": top1_hits,
        "by_race": by_race,
    }


def main() -> None:
    labels = _labels()
    races = [race for race_id, label in sorted(labels.items()) if (race := _race(race_id, label))]
    races.sort(key=lambda race: (race["race_date"], race["race_id"]))
    train = [race for race in races if race["race_date"] <= TRAIN_THROUGH]
    test = [race for race in races if race["race_date"] > TRAIN_THROUGH]
    if not train or not test:
        raise ValueError("both chronological train and test races are required")

    baseline_train = _metrics(train, None)
    baseline_test = _metrics(test, None)
    candidates = {}
    for feature in ("rest_interval", "same_ground_front4"):
        selected = min(BETAS, key=lambda beta: (_metrics(train, feature, beta)["mean_log_loss"], abs(beta)))
        train_metrics = _metrics(train, feature, selected)
        test_metrics = _metrics(test, feature, selected)
        candidates[feature] = {
            "selected_beta_on_train": selected,
            "train": train_metrics,
            "test": test_metrics,
            "test_delta_mean_log_loss": test_metrics["mean_log_loss"] - baseline_test["mean_log_loss"],
            "test_delta_mean_brier_sum": test_metrics["mean_brier_sum"] - baseline_test["mean_brier_sum"],
        }

    result = {
        "method": "frozen pre-post probabilities, normalized exponential feature tilt, chronological holdout",
        "train_through": TRAIN_THROUGH,
        "coefficient_grid": BETAS,
        "train_race_ids": [race["race_id"] for race in train],
        "test_race_ids": [race["race_id"] for race in test],
        "baseline": {"train": baseline_train, "test": baseline_test},
        "candidates": candidates,
        "races": [{key: value for key, value in race.items() if key not in {"baseline", "features"}} for race in races],
        "unavailable": {
            "opponent_strength": "history rows have no verified opponent-strength rating at snapshot time",
            "cushion_and_moisture": "frozen prediction snapshots store weather and track condition only",
        },
        "limitations": [
            "Small, nonrandom set of races with frozen predictions and result labels.",
            "Exponential tilt is a probability diagnostic, not an end-to-end ticket backtest.",
            "Same-ground fourth-corner position overlaps the existing front-rate feature.",
            "Winning-race outcomes are never used as feature inputs; all history dates are checked.",
        ],
    }
    output = HERE / "evaluation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output), "train": len(train), "test": len(test),
        "baseline_test": baseline_test["mean_log_loss"],
        "candidates": {
            name: {"beta": value["selected_beta_on_train"], "test_log_loss": value["test"]["mean_log_loss"],
                   "delta": value["test_delta_mean_log_loss"]}
            for name, value in candidates.items()
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
