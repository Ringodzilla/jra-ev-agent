"""Preserve the meaning of official historical sectionals and corner observations.

Column labels establish units. A jump race or an unusually small number alone
does not establish that a value is an average furlong time.
"""
from __future__ import annotations

import re
import unicodedata
import math


SEMANTIC_FIELDS = (
    "last_3f_status", "last_3f_missing_reason", "average_1f", "average_1f_status",
    "sectional_header", "sectional_raw", "average_1f_header", "average_1f_raw", "passing_order_status",
    "passing_order_missing_reason", "passing_order_raw", "official_course_text",
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def sectional_observation(raw: str, header: str, *, jump: bool = False,
                         position: str = "") -> dict[str, str]:
    """Interpret an observed label; never infer units from a numerical threshold."""
    label = _compact(header)
    value_label = _compact(raw)
    # Unit labels themselves contain digits. Strip known labels, then require
    # one complete positive value; never turn '3F 計不' into a time of three.
    unlabelled = re.sub(r"平均1(?:F|ハロン)|(?:推定)?(?:上り|上がり|末|後)?3F|(?:推定)?(?:上り|上がり)", "", value_label)
    unlabelled = unlabelled.removesuffix("秒")
    numeric = bool(re.fullmatch(r"\d+(?:\.\d+)?", unlabelled))
    value = unlabelled if numeric and math.isfinite(float(unlabelled)) and float(unlabelled) > 0 else ""
    result = {"last_3f": "", "last_3f_source": "", "last_3f_status": "UNVERIFIED",
              "last_3f_missing_reason": "UNVERIFIED_SECTIONAL_LABEL", "average_1f": "",
              "average_1f_status": "NOT_OBSERVED", "sectional_header": header,
              "sectional_raw": raw, "average_1f_header": "", "average_1f_raw": ""}
    header_average = "平均1F" in label or "平均1ハロン" in label
    value_average = "平均1F" in value_label or "平均1ハロン" in value_label
    header_3f = bool(re.search(r"(?:^|[^\d])3F", label))
    value_3f = bool(re.search(r"(?:^|[^\d])3F", value_label))
    if ((header_average and (header_3f or value_3f))
            or (value_average and (header_3f or value_3f))):
        result.update(last_3f_missing_reason="CONFLICTING_SECTIONAL_UNITS",
                      average_1f_status="UNVERIFIED")
        return result
    if header_average or value_average:
        result.update(average_1f=value, average_1f_status="OBSERVED" if value else "NOT_OBSERVED",
                      last_3f_status="NOT_APPLICABLE",
                      last_3f_missing_reason="RESULT_COLUMN_IS_AVERAGE_1F",
                      average_1f_header=header, average_1f_raw=raw)
        return result
    explicit_3f = header_3f or value_3f
    known_last3f = label in {"上り", "上がり", "推定上り", "推定上がり", "末3F", "後3F"}
    if explicit_3f or (known_last3f and not jump):
        if value:
            result.update(last_3f=value, last_3f_source="observed", last_3f_status="OBSERVED",
                          last_3f_missing_reason="")
        else:
            result.update(last_3f_status="NOT_OBSERVED", last_3f_missing_reason=(
                "NONFINISHER_SECTIONAL_NOT_OBSERVED" if "中止" in position
                else "OFFICIAL_SECTIONAL_CELL_EMPTY"))
    elif not value and "中止" in position:
        result.update(last_3f_status="NOT_OBSERVED",
                      last_3f_missing_reason="NONFINISHER_SECTIONAL_NOT_OBSERVED")
    return result


def corner_observation(raw: str, course_text: str, *, values: list[str] | None = None,
                       position: str = "") -> dict[str, str]:
    """A straight-course absence is nonapplicable, not a neutral running style."""
    raw_valid = raw.strip() in {"", "-", "―", "—", "計不", "中止"} or bool(
        re.fullmatch(r"\s*\d+(?:[\s,\-→]+\d+)*\s*", raw))
    parts = values if values is not None else re.findall(r"\d+", raw) if raw_valid else []
    valid = all(re.fullmatch(r"\d+", part) and int(part) > 0 for part in parts)
    passing = "-".join(parts)
    straight = "直線" in course_text or bool(re.search(r"芝\s*[・･]\s*直", course_text))
    if not valid or (values is None and not raw_valid):
        passing, state, reason = "", "UNVERIFIED", "UNVERIFIED_CORNER_VALUES"
    elif passing:
        state, reason = "OBSERVED", ""
    elif straight:
        state, reason = "NOT_APPLICABLE", "STRAIGHT_COURSE_NO_CORNERS"
    else:
        state, reason = "NOT_OBSERVED", (
            "NONFINISHER_CORNER_NOT_OBSERVED" if "中止" in position
            else "OFFICIAL_CORNER_OBSERVATION_MISSING")
    return {"passing_order": passing, "passing_order_status": state,
            "passing_order_missing_reason": reason, "passing_order_raw": raw,
            "official_course_text": course_text}


def parse_result_history_semantics(soup, row) -> dict[str, str]:
    """Read JRA result-row facts using the matching table's actual header."""
    table = row.find_parent("table")
    headers = table.select("th.f_time") if table else []
    header = headers[0].get_text(" ", strip=True) if len(headers) == 1 else ""
    if not header and table:
        cells = row.find_all(["td", "th"], recursive=False)
        time_cell = row.select_one("td.f_time")
        index = cells.index(time_cell) if time_cell in cells else -1
        for tr in table.select("tr"):
            columns = tr.find_all("th", recursive=False)
            if index >= 0 and len(columns) == len(cells):
                header = columns[index].get_text(" ", strip=True)
                break
    cell = row.select_one("td.f_time")
    raw = cell.get_text(" ", strip=True) if cell else ""
    course = soup.select_one(".cell.course")
    course_text = course.get_text(" ", strip=True) if course else ""
    place = row.select_one("td.place")
    position = place.get_text(" ", strip=True) if place else ""
    jump = "障害" in course_text or "芝→ダート" in _compact(course_text)
    result = sectional_observation(raw, header, jump=jump, position=position)
    if result["last_3f_status"] == "OBSERVED":
        result["last_3f_source"] = "official_historical_result"
    corner = row.select_one("td.corner")
    corner_raw = corner.get_text(" ", strip=True) if corner else ""
    values = [node.get_text(strip=True) for node in row.select("td.corner li")]
    result.update(corner_observation(corner_raw, course_text,
                                    values=values if values else None, position=position))
    return result
