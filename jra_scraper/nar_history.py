"""Recover NAR (regional racing) past-run observations from keiba.go.jp.

The JRA horse-profile history lists a horse's NAR starts but publishes no
final-3F sectional or corner positions for them, so those fields arrive
as NOT_OBSERVED and block the audit gate as required missing data. The
fields are not absent from the world: the official NAR portal
(keiba.go.jp, KeibaWeb) publishes a full result table (RaceMarkTable)
for every NAR race with sectionals, corner positions, popularity, win
odds and weather. This module resolves one past run to its official
result page and parses it through
:func:`jra_scraper.foreign_history.parse_foreign_history_artifact`, the
same reparse the audit performs, so collection and audit can never
disagree about what the page says.

Resolution is discovery-based and verified at every step: the day's
per-track menu (RaceList) must name the expected track in its header and
list the expected race name; only then is the result page fetched. A
step that cannot be verified records an outcome and stops — nothing is
guessed and no value is manufactured here.

Track codes (k_babaCode) are the portal's own stable identifiers. Each
entry used must have been verified against a real menu page for a race
at that track (header track name + race name present); 大井=20 was
verified 2026-11-13 against the 2026-10-06 menu (レディスプレリュード).
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

PORTAL = "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo"
FIELDS = ("last_3f", "passing_order", "popularity", "odds", "weather")

# Portal track codes, keyed by the course string the JRA profile uses
# (名古屋 is truncated to 名古 there). Values are (k_babaCode, canonical
# track name for menu-header verification); a code of None means the
# track is NAR but its code is not yet verified, so no route is attempted.
# Only codes verified against a live menu page for a race at that track
# belong here (see module docstring).
NAR_TRACKS: dict[str, tuple[int | None, str]] = {
    # Verified 2026-11-13 against live menus for real graded races:
    # 大井 レディスプレリュード, 川崎 エンプレス杯/スパーキングレディーカップ,
    # 船橋 クイーン賞, 浦和 さきたま杯/オーバルスプリント,
    # 名古屋 名古屋大賞典/かきつばた記念, 盛岡 不来方賞,
    # 高知 黒船賞, 佐賀 サマーチャンピオン (金沢・門別は同日の
    # メニューヘッダで確認).
    "大井": (20, "大井"),
    "川崎": (21, "川崎"),
    "船橋": (19, "船橋"),
    "浦和": (18, "浦和"),
    "名古": (24, "名古屋"),
    "盛岡": (10, "盛岡"),
    "門別": (36, "門別"),
    "金沢": (22, "金沢"),
    "高知": (31, "高知"),
    "佐賀": (32, "佐賀"),
    # NAR tracks whose portal code is not yet verified: no route attempted.
    "水沢": (None, "水沢"),
    "笠松": (None, "笠松"),
    "園田": (None, "園田"),
    "姫路": (None, "姫路"),
}


def _parse_run_date(value: str) -> tuple[str, str] | None:
    """Return (portal 'YYYY/MM/DD', ISO 'YYYY-MM-DD') for a row date."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return (f"{year:04d}/{month:02d}/{day:02d}",
            f"{year:04d}-{month:02d}-{day:02d}")


def is_nar_history_row(row: dict) -> bool:
    """A past run at an NAR track whose sectionals the profile cannot show.

    The JRA profile has no sectional column for NAR starts: at parse
    time such a run carries the parser's neutral 36.0 fallback (source
    'fallback') or an empty sectional, and no corner positions. Either
    missing piece justifies recovery; the merge keeps any value that
    was genuinely observed.
    """
    course = str(row.get("course", "")).strip()
    track = NAR_TRACKS.get(course)
    if track is None or track[0] is None:
        return False
    if _parse_run_date(str(row.get("date", ""))) is None:
        return False
    if not re.fullmatch(r"\d+", str(row.get("position", "")).strip()):
        return False
    sectional_missing = (str(row.get("last_3f_source", "")).strip() == "fallback"
                         or not str(row.get("last_3f", "")).strip())
    corners_missing = not str(row.get("passing_order", "")).strip()
    return sectional_missing or corners_missing


def menu_url(portal_date: str, baba_code: int) -> str:
    return (f"{PORTAL}/RaceList?k_raceDate={quote(portal_date, safe='')}"
            f"&k_babaCode={baba_code}")


def result_url(portal_date: str, baba_code: int, race_no: int) -> str:
    return (f"{PORTAL}/RaceMarkTable?k_raceDate={quote(portal_date, safe='')}"
            f"&k_babaCode={baba_code}&k_raceNo={race_no}")


def _normalize_name(text: str) -> str:
    name = re.sub(r"[\s　]", "", str(text or ""))
    return re.sub(r"^第\d+回", "", name)


def find_race_no(menu_html: str, race_name: str, course: str) -> int | None:
    """Locate a race in the day's per-track menu.

    Returns the race number only when the menu header names the expected
    track and a listed race matches the expected name. The JRA profile
    truncates race names (レディスプレ, ジャパンDC), so after an exact
    substring pass, a unique row sharing the name's first four characters
    is accepted; ambiguity or absence returns None.
    """
    soup = BeautifulSoup(menu_html, "html.parser")
    text = soup.get_text(" ", strip=True)
    if f"{course}競馬" not in text and f"{course} 第" not in text:
        return None
    target = _normalize_name(race_name)
    if not target:
        return None
    candidates = []
    for row in soup.select("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        match = re.fullmatch(r"(\d{1,2})R", cells[0].get_text(strip=True))
        if not match:
            continue
        names = _normalize_name(row.get_text(" ", strip=True))
        candidates.append((int(match.group(1)), names))
    for race_no, names in candidates:
        if len(target) >= 3 and target in names:
            return race_no
    prefix = [race_no for race_no, names in candidates
              if len(target) >= 4 and target[:4] in names]
    if len(prefix) == 1:
        return prefix[0]
    return None


def decode_page(content: bytes) -> str:
    """Decode a KeibaWeb page (Shift_JIS family) without garbling text."""
    head = content[:4096].decode("ascii", errors="ignore").lower()
    if "utf-8" in head or "utf8" in head:
        return content.decode("utf-8", errors="replace")
    return content.decode("cp932", errors="replace")


def collect_nar_history(row: dict, *, fetch_bytes, menu_cache: dict | None = None) -> dict:
    """Resolve and parse one NAR past run. Never raises for route failures.

    Returns {'attempt': {...}, 'artifact': dict|None,
    'observation': dict|None}. ``fetch_bytes(url)`` returns raw bytes or
    None. ``menu_cache`` maps (portal_date, baba_code) to menu HTML so a
    card shared by several horses is fetched once per run.
    """
    from jra_scraper.foreign_history import parse_foreign_history_artifact

    course = str(row.get("course", "")).strip()
    track = NAR_TRACKS.get(course)
    parsed_date = _parse_run_date(str(row.get("date", "")))
    attempt = {"route": "nar_keiba_result", "fields": list(FIELDS),
               "outcome": "", "reason": "", "url": ""}
    if parsed_date is None or track is None or track[0] is None:
        attempt.update(outcome="route_unresolved",
                       reason="No verified portal track code or parseable date for this run")
        return {"attempt": attempt, "artifact": None, "observation": None}
    portal_date, iso_date = parsed_date
    baba_code, canonical_course = track

    cache = menu_cache if menu_cache is not None else {}
    key = (portal_date, baba_code)
    menu_html = cache.get(key)
    if menu_html is None:
        url = menu_url(portal_date, baba_code)
        attempt["url"] = url
        raw = fetch_bytes(url)
        if raw is None:
            attempt.update(outcome="fetch_failed", reason="Portal day menu unavailable")
            return {"attempt": attempt, "artifact": None, "observation": None}
        menu_html = decode_page(raw)
        cache[key] = menu_html
    race_no = find_race_no(menu_html, str(row.get("race_name", "")), canonical_course)
    if race_no is None:
        attempt.update(outcome="race_not_found",
                       reason="Race not listed on the verified portal day menu")
        return {"attempt": attempt, "artifact": None, "observation": None}

    url = result_url(portal_date, baba_code, race_no)
    attempt["url"] = url
    raw = fetch_bytes(url)
    if raw is None:
        attempt.update(outcome="fetch_failed", reason="Official result page unavailable")
        return {"attempt": attempt, "artifact": None, "observation": None}
    html = decode_page(raw)
    artifact = {
        "kind": "nar_history_result",
        "url": url,
        "race_id": str(row.get("race_id", "")),
        "horse_id": str(row.get("horse_id", "")),
        "horse_name": str(row.get("horse_name", "")),
        "date": iso_date,
        "html": html,
        "sha256": hashlib.sha256(html.encode()).hexdigest(),
    }
    try:
        observation = parse_foreign_history_artifact(html, artifact)[0]
    except (ValueError, KeyError, TypeError) as exc:
        attempt.update(outcome="parse_failed", reason=str(exc))
        return {"attempt": attempt, "artifact": None, "observation": None}
    attempt.update(outcome="observed",
                   artifact_sha256=artifact["sha256"])
    return {"attempt": attempt, "artifact": artifact, "observation": observation}
