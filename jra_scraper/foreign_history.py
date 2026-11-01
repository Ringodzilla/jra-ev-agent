"""Observed NAR, HKJC and JRA overseas history, without numerical fallbacks."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


HOSTS = {
    "france_tracking_pdf": {"www7.france-galop.com", "www.france-galop.com", "france-galop.com"},
    "nar_history_result": {"www.keiba.go.jp", "keiba.go.jp"},
    "hkjc_history_sectionals": {"racing.hkjc.com"},
    "overseas_jra_result": {"www.jra.go.jp", "jra.go.jp", "jra.jp", "www.jra.jp"},
}


def _one(nodes, label):
    if len(nodes) != 1:
        raise ValueError("Nonunique official " + label)
    return nodes[0]


def _positive(text):
    value = text.strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", value) or Decimal(value) <= 0:
        raise ValueError("Unobserved positive number")
    return value


def _japanese_date(text):
    match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if not match:
        raise ValueError("Official date missing")
    return date(*map(int, match.groups())).isoformat()


def _text(node, selector):
    found = _one(node.select(selector), selector)
    return found.get_text(" ", strip=True)


def _identity(artifact, observed_day):
    if observed_day != date.fromisoformat(artifact["date"]).isoformat():
        raise ValueError("Official history date mismatch")
    return {key: str(artifact[key]) for key in ("race_id", "horse_id", "horse_name")} | {"date": observed_day}


def _jra_identity(artifact, *, number, position, distance, track, race_name):
    """Bind the overseas numbered runner to a SHA-bound JRA registered horse.

    English names and caller-provided aliases are not identity evidence. A
    second official result must agree on day, race, track, distance, number and
    finish while linking the exact Japanese registered horse ID.
    """
    source = artifact["identity_source"]
    html = source["html"]
    if (urlparse(source["url"]).hostname not in HOSTS["overseas_jra_result"]
            or hashlib.sha256(html.encode()).hexdigest() != source["sha256"]):
        raise ValueError("JRA identity source is not hash-bound and official")
    soup = BeautifulSoup(html, "html.parser")
    day_text = _text(soup, ".cell.date")
    if _japanese_date(day_text) != artifact["date"]:
        raise ValueError("JRA identity source date mismatch")
    translations = {"SHA TIN": ("シャティン", "THE FWD QEII CUP", "クイーンエリザベス2世カップ"),
                    "PARISLONGCHAMP": ("パリロンシャン", "QATAR PRIX DE L'ARC DE TRIOMPHE", "凱旋門賞")}
    if track not in translations:
        raise ValueError("Unverified overseas venue translation")
    jp_track, official_race, jp_race = translations[track]
    titles = [node.get_text(" ", strip=True) for node in soup.select("h1,h2")]
    if jp_track not in day_text or race_name != official_race or not any(jp_race in title for title in titles):
        raise ValueError("JRA identity race/venue mismatch")
    course = re.search(r"([\d,]+)\s*メートル", _text(soup, ".cell.course"))
    if not course or int(course[1].replace(",", "")) != int(distance):
        raise ValueError("JRA identity distance mismatch")
    row = _one([tr for tr in soup.select("tr") if tr.select_one("td.horse a")
                and tr.select_one("td.horse a").get_text(strip=True) == artifact["horse_name"]], "JRA identity horse")
    link = row.select_one("td.horse a")["href"]
    ids = parse_qs(urlparse(link).query).get("CNAME", [])
    if ids != [artifact["horse_id"]]:
        raise ValueError("JRA registered horse ID mismatch")
    if (_text(row, "td.num .cell.num") != str(number)
            or _text(row, "td.place") != str(position)):
        raise ValueError("JRA identity runner number or finish mismatch")


def _nar(soup, artifact):
    text = soup.get_text(" ", strip=True)
    result = _identity(artifact, _japanese_date(text))
    row = _one([tr for tr in soup.select("tr") if tr.select_one("td.horseName")
                and tr.select_one("td.horseName").get_text(strip=True) == artifact["horse_name"]], "horse")
    # Header meanings, rather than the apparent scale of the number, bind units.
    table = row.find_parent("table")
    headings = table.get_text(" ", strip=True)
    if not re.search(r"上がり\s*3F", headings) or not re.search(r"コーナー\s*通過順", headings):
        raise ValueError("NAR sectional/corner units unverified")
    for field, selector in (("position", "td.a"), ("weight", "td.g"),
                            ("last_3f", "td.m"), ("popularity", "td.o"), ("odds", "td.p")):
        result[field] = _positive(_text(row, selector))
    result["time"] = _text(row, "td.k")
    if not re.fullmatch(r"\d+:\d{2}(?:\.\d+)?", result["time"]):
        raise ValueError("NAR elapsed time unverified")
    corners = _text(row, "td.n")
    if not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)*", corners):
        raise ValueError("NAR corners unobserved")
    result.update(passing_order=corners, passing_order_status="OBSERVED",
                  last_3f_status="OBSERVED", last_3f_source="official_nar_result",
                  last_3f_missing_reason="", passing_order_missing_reason="")
    weather = re.search(r"天候[：:]\s*([^\s]+)", text)
    if weather:
        result["weather"] = weather[1]
    return result


def _hkjc(soup, artifact):
    text = soup.get_text(" ", strip=True)
    meeting = re.search(r"Meeting Date:\s*(\d{2})/(\d{2})/(\d{4})", text)
    if not meeting:
        raise ValueError("HKJC meeting date missing")
    dd, mm, yyyy = map(int, meeting.groups())
    result = _identity(artifact, date(yyyy, mm, dd).isoformat())
    race = _one(soup.select("#Race" + str(int(artifact["race_number"]))), "HKJC race")
    conditions = _text(race, ".Race")
    distance = re.search(r"\b(\d+)M\b", conditions)
    if not distance or int(distance[1]) < 800 or int(distance[1]) % 400:
        raise ValueError("HKJC 400m interval geometry unsupported")
    if "expected_distance" in artifact and int(distance[1]) != int(artifact["expected_distance"]):
        raise ValueError("HKJC distance mismatch")
    official_name = artifact["official_horse_name"]
    rows = [tr for tr in race.select("table.race_table tbody tr") if tr.select_one("a")
            and re.sub(r"\s*\([^)]*\)\s*$", "", tr.select_one("a").get_text(" ", strip=True)) == official_name]
    row = _one(rows, "HKJC horse")
    cells = row.find_all("td", recursive=False)
    if len(cells) != 10:
        raise ValueError("HKJC sectional row schema changed")
    sections = cells[3:9]
    observed = [cell for cell in sections if cell.select_one("p.f_clear")]
    if len(observed) != int(distance[1]) // 400:
        raise ValueError("HKJC interval count mismatch")
    final800 = observed[-2:]
    splits = []
    for cell in final800:
        node = _one(cell.select("p.sectional_200"), "explicit 200m section")
        pair = [_positive(span.get_text(strip=True)) for span in node.select("span.color_blue2 > span")]
        if len(pair) != 2:
            raise ValueError("HKJC 200m pair incomplete")
        aggregate = node.get_text(" ", strip=True).split()[0]
        if Decimal(_positive(aggregate)) != sum(map(Decimal, pair)):
            raise ValueError("HKJC component sum mismatch")
        splits.extend(pair)
    last600 = sum(map(Decimal, splits[-3:]))
    meeting_node = _one(soup.find_all(string=re.compile(r"Meeting Date:")), "HKJC meeting label")
    meeting_track = re.search(r"Meeting Date:\s*\d{2}/\d{2}/\d{4},\s*([A-Za-z ]+)\s*$", str(meeting_node))
    if not meeting_track:
        raise ValueError("HKJC meeting venue missing")
    # The final span in the race conditions contains the official race name.
    spans = race.select(".Race > p.f_fl > span")
    if len(spans) != 2:
        raise ValueError("HKJC race title unverified")
    _jra_identity(artifact, number=cells[1].get_text(strip=True), position=cells[0].get_text(strip=True),
                  distance=int(distance[1]), track=meeting_track[1].strip().upper(),
                  race_name=spans[-1].get_text(" ", strip=True))
    result.update(position=_positive(cells[0].get_text(strip=True)), time=cells[-1].get_text(strip=True),
                  last_3f=str(last600), last_3f_status="OBSERVED",
                  last_3f_source="official_hkjc_individual_200m_sum",
                  last_3f_missing_reason="", individual_final800m_200m_splits=splits,
                  running_positions_at_400m_boundaries=[_text(cell, "p.f_clear span.f_fl") for cell in observed],
                  sectional_scope="individual_final600m", sectional_transformation="sum_last_three_official_200m_intervals")
    return result


def _overseas_jra(soup, artifact):
    source_day = _text(soup, ".cell.date")
    result = _identity(artifact, _japanese_date(source_day))
    row = _one([tr for tr in soup.select("tr") if tr.select_one("td.horse")
                and re.sub(r"\([^)]*\)", "", tr.select_one("td.horse").get_text(strip=True)) == artifact["horse_name"]], "overseas horse")
    for field, selector in (("position", "td.place"), ("popularity", "td.pop"), ("weight", "td.weight")):
        result[field] = _positive(_text(row, selector))
    # td.time is often a margin, not elapsed time. Never copy it as clock time.
    return result


def _france_tracking(text, artifact):
    # The envelope contains derived text, not the primary binary. Reproduce it
    # from the SHA-bound PDF on every use so text cannot be edited to pass audit.
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    try:
        raw = Path(artifact["pdf_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact["pdf_sha256"]:
            raise ValueError("France Galop PDF bytes changed")
        reader = PdfReader(artifact["pdf_path"])
    except (OSError, PyPdfError) as exc:
        raise ValueError("France Galop PDF cannot be read") from exc
    if artifact.get("pdf_page") != 0 or not reader.pages:
        raise ValueError("France Galop summary page is required")
    extracted = reader.pages[0].extract_text(extraction_mode="layout")
    if extracted != text:
        raise ValueError("France Galop extracted text differs from binary source")
    months = {"janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
              "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12}
    day = re.search(r"\b(\d{1,2}) (" + "|".join(months) + r") (\d{4})\b", text)
    if not day:
        raise ValueError("France Galop source date missing")
    result = _identity(artifact, date(int(day[3]), months[day[2]], int(day[1])).isoformat())
    if artifact["expected_course"] not in text.splitlines()[1].strip():
        raise ValueError("France Galop course mismatch")
    race = re.search(r"C(\d+) - (.+) - (\d+)m", text)
    if (not race or int(race[1]) != int(artifact["race_number"])
            or int(race[3]) != int(artifact["expected_distance"])):
        raise ValueError("France Galop race or distance mismatch")
    if not re.search(r"Temps\s+Derniers\s+Parcouru", text) or not re.search(r"officiel\s+600m", text):
        raise ValueError("France Galop last600m header unverified")
    lines = [line for line in text.splitlines() if re.match(
        r"\s*\d+\s+" + re.escape(artifact["official_horse_name"]) + r"\s+\d+\s", line)]
    row = _one(lines, "France Galop runner")
    values = re.findall(r"\b\d{2}:\d{2}\.\d{2}\b", row)
    if len(values) != 8:
        raise ValueError("France Galop tracking row schema changed")
    minute, seconds = values[-1].split(":")
    last600 = Decimal(minute) * 60 + Decimal(seconds)
    runner = re.match(r"\s*(\d+)\s+" + re.escape(artifact["official_horse_name"]) + r"\s+(\d+)\s", row)
    _jra_identity(artifact, number=runner[1], position=runner[2], distance=int(race[3]),
                  track=text.splitlines()[1].strip(), race_name=race[2])
    result.update(last_3f=_positive(str(last600)), last_3f_status="OBSERVED",
                  last_3f_source="official_france_galop_tracking_last600m",
                  last_3f_missing_reason="", sectional_scope="individual_final600m",
                  sectional_transformation="official_Derniers_600m_aggregate")
    return result


def parse_foreign_history_artifact(html: str, artifact: dict) -> list[dict]:
    """Reparse official raw HTML; retain undefined fields as absent, never impute.

    Artifact identity requires date ISO, race_id, horse_id and horse_name. HKJC
    also requires race_number and official_horse_name. The raw SHA is validated
    by the caller before this parser is called.
    """
    kind = artifact.get("kind")
    if kind not in HOSTS or urlparse(artifact.get("url", "")).hostname not in HOSTS[kind]:
        raise ValueError("Unsupported official history source")
    if kind == "france_tracking_pdf":
        return [_france_tracking(html, artifact)]
    soup = BeautifulSoup(html, "html.parser")
    parser = {"nar_history_result": _nar, "hkjc_history_sectionals": _hkjc,
              "overseas_jra_result": _overseas_jra}[kind]
    return [parser(soup, artifact)]
