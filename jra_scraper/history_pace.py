"""Exact race-wide opening sectionals from official JRA result intervals."""
from __future__ import annotations

from decimal import Decimal
import re

from bs4 import BeautifulSoup


OFFICIAL_INTERVAL_RULE = "https://www.jra.go.jp/kouza/yougo/w291.html"


def parse_official_opening_600m(html: str, *, expected_distance: int | None = None) -> dict:
    """Sum observed intervals only when 600m is an official timing boundary.

    JRA times the leader at each boundary, not an individual runner. Odd 100m
    distances begin with 100m; their 500m/700m split cannot identify 600m.
    """
    result = {"pace": "", "pace_source": "official_race_leader_intervals",
              "pace_scope": "race_leader_at_each_boundary",
              "pace_rule_url": OFFICIAL_INTERVAL_RULE}
    soup = BeautifulSoup(html, "html.parser")
    courses = soup.select(".cell.course")
    distances = [re.search(r"([0-9][0-9,]*)\s*メートル", node.get_text(" ", strip=True))
                 for node in courses]
    if len(distances) != 1 or distances[0] is None:
        return dict(result, pace_missing_reason="MISSING_OR_AMBIGUOUS_DISTANCE")
    distance = int(distances[0].group(1).replace(",", ""))
    result["distance_m"] = distance
    if expected_distance is not None and distance != expected_distance:
        return dict(result, pace_missing_reason="DISTANCE_MISMATCH")
    if distance < 600 or distance % 100:
        return dict(result, pace_missing_reason="UNSUPPORTED_DISTANCE")
    cells = [row.find("td") for row in soup.select("tr")
             if row.find("th") and row.find("th").get_text(strip=True) == "ハロンタイム"]
    if len(cells) != 1 or cells[0] is None:
        return dict(result, pace_missing_reason="MISSING_OR_AMBIGUOUS_INTERVALS")
    text = cells[0].get_text(" ", strip=True)
    if not re.fullmatch(r"\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)*", text):
        return dict(result, pace_missing_reason="INVALID_INTERVALS")
    laps = [Decimal(part.strip()) for part in text.split("-")]
    lengths = ([100] if distance % 200 else []) + [200] * (distance // 200)
    if len(laps) != len(lengths) or any(value <= 0 for value in laps):
        return dict(result, pace_missing_reason="INVALID_INTERVAL_COUNT_OR_VALUE")
    result["interval_seconds"] = [str(value) for value in laps]
    result["interval_lengths_m"] = lengths
    boundary = 0
    elapsed = Decimal(0)
    for length, value in zip(lengths, laps):
        boundary += length
        elapsed += value
        if boundary == 600:
            return dict(result, pace=format(elapsed, "f").rstrip("0").rstrip(".")
                        if "." in format(elapsed, "f") else format(elapsed, "f"),
                        pace_missing_reason="")
        if boundary > 600:
            break
    return dict(result, pace_missing_reason="NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED")
