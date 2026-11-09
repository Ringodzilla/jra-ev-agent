"""France Galop tracking collector for French history rows (Issue #39-A).

This module is the *data_collector* half of the France route: it finds the
official France Galop tracking summary PDF for one already-collected history
row, stores the binary, and builds the SHA-bound artifact envelope that
:func:`jra_scraper.foreign_history.parse_foreign_history_artifact` reparses.
It never invents values: every failure branch returns ``artifact=None`` (or a
stored artifact whose reparse failed, reported honestly) together with an
attempt record saying exactly what was tried and why it stopped. No numeric
fallback, no imputation, no identity by English name alone.

Identity binding and the Prix Foy limitation
--------------------------------------------
The reparse normally binds the tracking runner through an independent JRADB
overseas result page. Its opaque URL must be supplied through the identity map.
For the Prix Foy, the same map accepts a JRADB bilingual horse profile with
the exact registered ID, name, race date, distance and finish. The tracking
PDF still supplies the race venue and exact timing observations. The
collector stops if the mapped source is missing or cannot be verified.

Design: pure / injectable. Logic functions perform no hard network calls;
``fetch_bytes`` / ``fetch_text`` / ``fetch_identity`` are callables supplied
by the caller, so tests never touch the network.
"""
from __future__ import annotations

from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re

from jra_scraper.foreign_history import parse_foreign_history_artifact
from jra_scraper.validation import _normalize_int


# JRA course markers -> (expected_course in the tracking PDF, venue code in
# the tracking PDF URL). Only explicit venue markers are keys: a bare "仏"
# means "somewhere in France" and is ambiguous on its own (see
# _BARE_FRANCE_RACE_VENUES below).
#
# Live-verification status (2026-11-08 probing): only LON
# (ParisLongchamp) was verified against real tracking PDFs. The other codes
# follow the France Galop venue-code convention but were NOT live-verified;
# STC (Saint-Cloud) in particular is UNVERIFIED. An unlisted venue resolves
# to None (fail closed) rather than a guessed code.
VENUES = {
    "パリロンシャン": ("PARISLONGCHAMP", "LON"),
    "ロンシャン": ("PARISLONGCHAMP", "LON"),
    "シャンティイ": ("CHANTILLY", "CHA"),  # not live-verified
    "ドーヴィル": ("DEAUVILLE", "DEA"),  # not live-verified
    "クレールフォンテーヌ": ("CLAIREFONTAINE", "CLA"),  # not live-verified
    "サンクルー": ("SAINT-CLOUD", "STC"),  # UNVERIFIED code
}
VENUE_MAP = VENUES
FRANCE_VENUES = VENUES

# Bare "仏" rows: only race names whose venue was actually verified during
# the 2026-11-08 probing may resolve -- both are ParisLongchamp races and
# both tracking PDFs were retrieved from the LON directory (the Arc,
# 2025-10-05 C5, and the Prix Foy, 2025-09-07 C2). Any other race name with a
# bare "仏" course stays unresolved instead of being guessed as French
# Longchamp (or as France Galop at all).
_BARE_FRANCE_RACE_VENUES = {
    "凱旋門賞": ("PARISLONGCHAMP", "LON"),
    "フォワ賞": ("PARISLONGCHAMP", "LON"),
}

# Reverse lookup, expected_course -> venue code, for artifact filenames.
_CODE_BY_COURSE = {course: code for course, code in VENUES.values()}

_MONTHS_FR = {"janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
              "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
              "décembre": 12}

_ATTEMPTED_FIELDS = ["last_3f", "time"]


def venue_for_row(row: dict) -> tuple[str, str] | None:
    """Resolve a history row to ``(expected_course, venue_code)`` or None.

    Explicit venue markers in the course fields win. A bare "仏" course
    resolves only in combination with a verified French race name (see
    ``_BARE_FRANCE_RACE_VENUES``); anything else returns None.
    """
    course_text = " ".join(str(row.get(key, "") or "")
                           for key in ("course", "official_course_text"))
    # Longest marker first so パリロンシャン is preferred over ロンシャン.
    for marker in sorted(VENUES, key=len, reverse=True):
        if marker in course_text:
            return VENUES[marker]
    if "仏" in course_text:
        return _BARE_FRANCE_RACE_VENUES.get(str(row.get("race_name", "") or "").strip())
    return None


# Alias: the venue resolution is a lookup, not a fetch.
lookup_venue = venue_for_row
resolve_venue = venue_for_row


def is_france_history_row(row: dict) -> bool:
    """True for a French-venue history row whose last_3f is not observed yet."""
    if venue_for_row(row) is None:
        return False
    if str(row.get("last_3f_status", "") or "").strip() == "OBSERVED":
        return False
    return True


def tracking_pdf_url(date_iso: str, code: str, race_number) -> str:
    """Verified pattern (2026-11-08): a direct PDF GET with a browser
    User-Agent returns 200 even though a plain www7 root request 403s."""
    day = date.fromisoformat(str(date_iso)).strftime("%Y%m%d")
    return (f"https://www7.france-galop.com/Casaques/Tracking/"
            f"{day}{code}{int(race_number):02d}_last_times_fr.pdf")


def extract_summary_text(pdf_bytes: bytes) -> str:
    """Extract page 0 of a tracking PDF in layout mode (same mode and
    library the reparse in ``foreign_history`` uses, so texts compare
    byte-for-byte)."""
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(pdf_bytes))
    if not reader.pages:
        raise ValueError("France Galop PDF has no pages")
    return reader.pages[0].extract_text(extraction_mode="layout")


def parse_summary_header(text: str) -> dict:
    """Parse the summary-page header: venue line, ``C<n> - title - <d>m``
    line and the French date line. Raises ValueError on anything missing."""
    lines = str(text).splitlines()
    if len(lines) < 2:
        raise ValueError("France Galop summary header missing")
    race = re.search(r"C(\d+) - (.+) - (\d+)m", text)
    if not race:
        raise ValueError("France Galop summary race line missing")
    day = re.search(r"\b(\d{1,2}) (" + "|".join(_MONTHS_FR) + r") (\d{4})\b", text)
    if not day:
        raise ValueError("France Galop summary date missing")
    return {"expected_course": lines[1].strip(),
            "race_number": int(race[1]),
            "race_title": race[2].strip(),
            "distance": int(race[3]),
            "date_iso": date(int(day[3]), _MONTHS_FR[day[2]], int(day[1])).isoformat()}


def _has_runner_row(text: str, official_horse_name: str) -> bool:
    pattern = r"\s*\d+\s+" + re.escape(official_horse_name) + r"\s+\d+\s"
    return any(re.match(pattern, line) for line in str(text).splitlines())


def probe_race(fetch_bytes, date_iso: str, code: str, *, distance,
               official_horse_name: str, race_numbers=range(1, 10)) -> dict | None:
    """Try candidate race numbers in order; accept the first PDF whose
    header distance equals ``distance`` AND whose text contains a runner
    row for ``official_horse_name``. Fetch/extract failures are recorded
    by omission and probing continues. No match -> None (never a guess)."""
    for race_number in race_numbers:
        url = tracking_pdf_url(date_iso, code, race_number)
        try:
            pdf_bytes = fetch_bytes(url)
        except Exception:
            continue
        if not pdf_bytes:
            continue
        try:
            text = extract_summary_text(pdf_bytes)
            header = parse_summary_header(text)
        except Exception:
            continue
        if header["distance"] != int(distance):
            continue
        if not _has_runner_row(text, official_horse_name):
            continue
        return {"race_number": int(race_number), "url": url,
                "pdf_bytes": pdf_bytes, "text": text, "header": header}
    return None


def _code_from_url(url: str) -> str | None:
    match = re.search(r"/\d{8}([A-Z]+)\d{2}_last_times_fr\.pdf$", str(url))
    return match[1] if match else None


def build_france_artifact(*, probe: dict, store_dir, race_id, horse_id, horse_name,
                          official_horse_name, date_iso, expected_course,
                          expected_distance, identity_source) -> dict:
    """Store the probed PDF and return the complete artifact dict in
    exactly the shape ``parse_foreign_history_artifact`` consumes.

    Envelope convention: top-level ``sha256`` is the SHA-256 of the
    extracted text (``html``), while ``pdf_sha256`` binds the stored PDF
    binary; ``identity_source`` is completed with the SHA-256 of its HTML
    when the caller supplied only ``{'url', 'html'}``.
    """
    race_number = int(probe["race_number"])
    code = _code_from_url(probe["url"]) or _CODE_BY_COURSE.get(expected_course)
    if not code:
        raise ValueError("France Galop venue code cannot be determined")
    pdf_bytes = probe["pdf_bytes"]
    directory = Path(store_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / f"france_tracking_{date_iso}_{code}{race_number:02d}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    text = probe["text"]
    identity = dict(identity_source)
    identity.setdefault("sha256", hashlib.sha256(str(identity.get("html", "")).encode()).hexdigest())
    return {"kind": "france_tracking_pdf",
            "url": probe["url"],
            "html": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "pdf_path": str(pdf_path),
            "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "pdf_page": 0,
            "date": str(date_iso),
            "race_id": race_id,
            "horse_id": horse_id,
            "horse_name": horse_name,
            "official_horse_name": official_horse_name,
            "expected_course": expected_course,
            "race_number": race_number,
            "expected_distance": int(expected_distance),
            "identity_source": identity}


def _row_date_iso(row: dict) -> str:
    raw = str(row.get("date", "") or "").strip()
    match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", raw)
    if match:
        return date(*map(int, match.groups())).isoformat()
    return date.fromisoformat(raw).isoformat()


def collect_france_history(row: dict, *, official_horse_name: str, fetch_bytes,
                           fetch_identity, store_dir) -> dict:
    """Collect one French history row end to end.

    Returns ``{'artifact': dict | None, 'observation': dict | None,
    'attempt': {...}}``. Every early exit is fail-closed: no artifact is
    built without a probed tracking PDF *and* an identity source, and an
    observation exists only when the existing parser reparses the stored
    artifact successfully.
    """
    attempt = {"route": "france_galop_tracking",
               "race_id": row.get("race_id"),
               "horse_id": row.get("horse_id"),
               "horse_number": row.get("horse_number"),
               "run_index": row.get("run_index"),
               "date": row.get("date"),
               "fields": [],
               "attempted_fields": list(_ATTEMPTED_FIELDS),
               "outcome": "",
               "reason": "",
               "probed_urls": [],
               "artifact_sha256": None}

    def _done(outcome, reason, *, artifact=None, observation=None):
        attempt.update(outcome=outcome, reason=reason)
        return {"artifact": artifact, "observation": observation, "attempt": attempt}

    venue = venue_for_row(row)
    if venue is None:
        return _done("jurisdiction_not_france",
                     "Row course/race does not identify a verified French venue; "
                     "a bare '仏' course without a verified French race name is ambiguous.")
    expected_course, code = venue
    try:
        date_iso = _row_date_iso(row)
        distance = int(_normalize_int(str(row.get("distance", "")).strip()))
    except (TypeError, ValueError) as exc:
        return _done("row_date_or_distance_unavailable",
                     f"Row date/distance cannot be read exactly: {exc}")

    race_numbers = range(1, 10)
    attempt["probed_urls"] = [tracking_pdf_url(date_iso, code, n) for n in race_numbers]
    probe = probe_race(fetch_bytes, date_iso, code, distance=distance,
                       official_horse_name=official_horse_name, race_numbers=race_numbers)
    if probe is None:
        return _done("tracking_pdf_unavailable",
                     "No candidate tracking PDF matched the row distance and runner; "
                     "probed URLs are listed in probed_urls.")

    try:
        identity = fetch_identity(date_iso, row.get("race_name"), row.get("horse_name"))
    except Exception as exc:  # an unfetchable identity source is unavailable
        identity = None
        identity_error = str(exc)
    else:
        identity_error = ""
    if not identity or not identity.get("url") or identity.get("html") is None:
        reason = ("No operator-supplied JRADB identity source for this race; "
                  "an artifact is never built without identity binding.")
        if identity_error:
            reason += f" Identity fetch failed: {identity_error}"
        return _done("identity_source_unavailable", reason)

    artifact = build_france_artifact(
        probe=probe, store_dir=store_dir, race_id=row.get("race_id"),
        horse_id=row.get("horse_id"), horse_name=row.get("horse_name"),
        official_horse_name=official_horse_name, date_iso=date_iso,
        expected_course=expected_course, expected_distance=distance,
        identity_source=identity)
    attempt["artifact_sha256"] = artifact["pdf_sha256"]
    try:
        parsed = parse_foreign_history_artifact(artifact["html"], artifact)
    except ValueError as exc:
        return _done("parse_failed", f"Stored artifact failed reparse: {exc}",
                     artifact=artifact)
    if len(parsed) != 1:
        return _done("parse_failed",
                     f"Stored artifact reparsed to {len(parsed)} observations, expected 1.",
                     artifact=artifact)
    observation = parsed[0]
    attempt["fields"] = [field for field in _ATTEMPTED_FIELDS if field in observation]
    return _done("observed",
                 "Tracking PDF and JRADB identity source reparsed successfully.",
                 artifact=artifact, observation=observation)


def load_identity_sources(path) -> dict:
    """Read the operator-maintained identity-source map:
    ``{'<date_iso>|<jp_race_name>': {'url': ...}}``. A missing file simply
    means no races have been supplied yet -> ``{}``."""
    source_path = Path(path)
    if not source_path.exists():
        return {}
    data = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Identity source map must be a JSON object")
    return data


def identity_fetcher_from_map(mapping: dict, fetch_text):
    """Build a ``fetch_identity(date_iso, race_name, horse_name)`` callable
    from an identity-source map and a text fetcher. Unmapped races and
    unfetchable pages both yield None (unavailable, never invented)."""
    def fetch_identity(date_iso, race_name, horse_name):
        entry = mapping.get(f"{date_iso}|{race_name}")
        if entry is None:
            return None
        url = entry.get("url") if isinstance(entry, dict) else entry
        if not url:
            return None
        try:
            html = fetch_text(url)
        except Exception:
            return None
        if html is None:
            return None
        return {"url": url, "html": html}

    return fetch_identity
