from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .models import HorseEntry, ParserIssue, RaceLink


TRACKS = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
logger = logging.getLogger(__name__)


@dataclass
class HeaderMatch:
    index: int
    canonical: str
    raw: str


class JRAParser:
    """Parser tuned for JRA/JRADB style HTML pages with repair-oriented fallbacks."""

    LAST_3F_NEUTRAL_BASELINE = "36.0"

    RACE_ANCHOR_SELECTORS = (
        'a[href*="/JRADB/accessD.html"]',
        'a[href*="pw01sde"]',
        'a[href*="pw01dde"]',
    )

    HISTORY_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
        "date": ("日付", "年月日"),
        "course": ("開催", "競馬場", "場"),
        "race_name": ("レース名", "レース", "競走名", "前走"),
        "position": ("着順", "着", "4走前"),
        "time": ("タイム", "走破タイム"),
        "weight": ("斤量",),
        "jockey": ("騎手", "騎手名"),
        "distance": ("距離", "前々走", "3走前"),
        "pace": ("前半", "前3F", "通過前半", "前半3F"),
        "last_3f": ("上り", "上り3F", "上がり", "上がり3F", "末3F", "後3F"),
        "track_condition": ("馬場", "馬場状態"),
        "weather": ("天候",),
        "passing_order": ("通過", "4角", "4C", "コーナー通過順"),
        "odds": ("単勝", "単勝オッズ", "オッズ"),
        "popularity": ("人気",),
    }

    ENTRY_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
        "frame_number": ("枠", "枠番"),
        "horse_number": ("馬番", "馬番号"),
        "horse_name": ("馬名", "馬"),
        "sex_age": ("性齢", "性/齢"),
        "assigned_weight": ("斤量", "負担重量"),
        "current_jockey": ("騎手", "騎手名"),
        "current_odds": ("単勝", "単勝オッズ", "オッズ"),
        "current_popularity": ("人気",),
    }

    REQUIRED_HISTORY_HEADERS = ("position", "last_3f", "popularity")

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def extract_initial_odds_cname(self, html: str) -> str:
        """Return the race-level win/place odds CNAME from a race detail page."""
        candidates = self.extract_odds_cnames(html)
        if candidates.get("win_place"):
            return candidates["win_place"]
        for cname in _extract_access_o_cnames(html):
            if "ouS" in cname:
                return cname
        return ""

    def extract_odds_cnames(self, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        out: dict[str, str] = {}
        label_map = {
            "単勝・複勝": "win_place",
            "単勝複勝": "win_place",
            "枠連": "wakuren",
            "馬連": "umaren",
            "ワイド": "wide",
            "馬単": "umatan",
            "3連複": "sanrenpuku",
            "３連複": "sanrenpuku",
            "三連複": "sanrenpuku",
            "3連単": "sanrentan",
            "３連単": "sanrentan",
            "三連単": "sanrentan",
        }
        for anchor in soup.select('a[onclick*="/JRADB/accessO.html"]'):
            onclick = anchor.get("onclick") or ""
            cname = _extract_cname_from_onclick(onclick)
            if not cname:
                continue
            label = self._norm(anchor.get_text(" ", strip=True)).replace(" ", "")
            key = label_map.get(label)
            if key and key not in out:
                out[key] = cname
        return out

    def parse_odds_page(
        self,
        html: str,
        *,
        race_id: str,
        source_cname: str = "",
        captured_at: str = "",
    ) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        rows: list[dict[str, str]] = []
        rows.extend(self._parse_win_place_odds(soup, race_id=race_id, source_cname=source_cname, captured_at=captured_at))
        rows.extend(self._parse_matrix_odds(soup, "wakuren", "waku", race_id, source_cname, captured_at))
        rows.extend(self._parse_matrix_odds(soup, "umaren", "umaren", race_id, source_cname, captured_at))
        rows.extend(self._parse_matrix_odds(soup, "wide", "wide", race_id, source_cname, captured_at, odds_range=True))
        rows.extend(self._parse_matrix_odds(soup, "umatan", "umatan", race_id, source_cname, captured_at, ordered=True))
        rows.extend(self._parse_matrix_odds(soup, "sanrenpuku", "fuku3", race_id, source_cname, captured_at, caption_pair=True))
        rows.extend(self._parse_trifecta_odds(soup, race_id=race_id, source_cname=source_cname, captured_at=captured_at))
        return rows

    def parse_race_list(self, html: str) -> list[RaceLink]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[RaceLink] = []

        for selector in self.RACE_ANCHOR_SELECTORS:
            for anchor in soup.select(selector):
                href = anchor.get("href") or ""
                race_name = " ".join(anchor.get_text(" ", strip=True).split())
                if not href or not race_name:
                    continue
                race_id = self._build_race_id(href, race_name)
                date, track, race_number = self._parse_race_id_components(race_id)
                links.append(
                    RaceLink(
                        race_id=race_id,
                        race_name=race_name,
                        race_url=urljoin(self.base_url, href),
                        race_date=date,
                        track=track,
                        race_number=race_number,
                    )
                )
        return self._dedupe_races(links)

    def parse_race_detail(
        self,
        html: str,
        race_id: str,
        race_name: str,
        *,
        target_race_date: str = "",
        target_track: str = "",
        target_race_number: str = "",
        target_surface: str = "",
        target_distance: str = "",
        issue_sink: list[ParserIssue] | None = None,
        aggressive_repair: bool = False,
    ) -> list[HorseEntry]:
        soup = BeautifulSoup(html, "html.parser")
        table = self._select_entry_table(soup)
        if table is None:
            self._issue(
                issue_sink,
                stage="parser.race_detail",
                severity="high",
                code="entry_table_missing",
                message="Could not find race entry table.",
                context={"race_id": race_id, "race_name": race_name},
            )
            raise ValueError("No horses parsed — selector broken")

        headers = self._extract_headers(table)
        matches = self._build_header_matches(headers, self.ENTRY_HEADER_ALIASES)
        entry_canonicals = {match.canonical for match in matches}
        rows = [tr for tr in table.select("tr") if tr.select("td")]
        header_meta = self._extract_race_header_metadata(soup)
        parsed_date, parsed_track, parsed_race_number = self._parse_race_id_components(race_id)
        target_date = parsed_date or header_meta.get("race_date", "") or target_race_date
        target_track = parsed_track or header_meta.get("track", "") or target_track
        target_race_number = parsed_race_number or header_meta.get("race_number", "") or target_race_number
        parsed_surface, parsed_distance = self._extract_race_conditions(soup)
        target_surface = parsed_surface or header_meta.get("target_surface", "") or target_surface
        target_distance = parsed_distance or header_meta.get("target_distance", "") or target_distance
        effective_race_name = self._resolve_race_name(race_name, header_meta.get("race_name", ""))

        horses: list[HorseEntry] = []
        for row_index, row in enumerate(rows, start=1):
            raw_cells = [self._norm(td.get_text(" ", strip=True)) for td in row.select("td")]
            cells = self._repair_cells(
                raw_cells,
                len(headers),
                matches,
                issue_sink,
                context={"race_id": race_id, "row_index": str(row_index)},
                aggressive=aggressive_repair,
            )
            mapped = self._map_row(matches, cells)
            mapped = self._apply_entry_fallbacks(
                mapped,
                row,
                raw_cells,
                aggressive_repair,
                allow_odds_fallback=("current_odds" in entry_canonicals),
            )

            anchor = row.select_one("a[href]")
            href = (anchor.get("href") or "").strip() if anchor else ""
            horse_name = (
                mapped.get("horse_name")
                or self._extract_text(row, "td.horse .name_line .name .txt")
                or (self._norm(anchor.get_text(" ", strip=True)) if anchor else "")
            )
            if not horse_name:
                self._issue(
                    issue_sink,
                    stage="parser.race_detail",
                    severity="medium",
                    code="entry_row_incomplete",
                    message="Skipped race entry row because horse name was missing.",
                    context={"race_id": race_id, "row_index": str(row_index)},
                )
                continue
            if not href:
                self._issue(
                    issue_sink,
                    stage="parser.race_detail",
                    severity="low",
                    code="entry_link_missing",
                    message="Horse link was missing; used fallback horse_id.",
                    context={"race_id": race_id, "row_index": str(row_index), "horse_name": horse_name},
                )

            horses.append(
                HorseEntry(
                    race_id=race_id,
                    race_name=effective_race_name,
                    horse_id=self._extract_horse_id(href, horse_name),
                    horse_name=horse_name,
                    horse_url=urljoin(self.base_url, href) if href else "",
                    frame_number=mapped.get("frame_number", ""),
                    horse_number=mapped.get("horse_number", ""),
                    current_jockey=mapped.get("current_jockey", ""),
                    assigned_weight=mapped.get("assigned_weight", ""),
                    current_odds=mapped.get("current_odds", ""),
                    current_popularity=mapped.get("current_popularity", ""),
                    target_track=target_track,
                    target_race_date=target_date,
                    target_race_number=target_race_number,
                    target_surface=target_surface,
                    target_distance=target_distance,
                    horse_country=mapped.get("horse_country", ""),
                    embedded_history=self._extract_embedded_history(row),
                )
            )

        horses = self._dedupe_horses(horses)
        if len(horses) == 0:
            raise ValueError("No horses parsed — selector broken")
        return horses

    def parse_horse_last5(
        self,
        html: str,
        race_id: str,
        horse_id: str,
        horse_name: str,
        horse_url: str,
        *,
        current_entry: HorseEntry | None = None,
        issue_sink: list[ParserIssue] | None = None,
        aggressive_repair: bool = False,
    ) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        table = self._select_last5_table(soup)
        if table is None:
            self._issue(
                issue_sink,
                stage="parser.horse_history",
                severity="high",
                code="history_table_missing",
                message="Could not find a horse history table.",
                context={"race_id": race_id, "horse_id": horse_id, "horse_name": horse_name},
            )
            logger.warning("No suitable last5 table found for horse=%s url=%s", horse_name, horse_url)
            return []

        headers = self._extract_headers(table)
        header_matches = self._build_header_matches(headers, self.HISTORY_HEADER_ALIASES)

        found_canonicals = {m.canonical for m in header_matches}
        for required in self.REQUIRED_HISTORY_HEADERS:
            if required not in found_canonicals:
                self._issue(
                    issue_sink,
                    stage="parser.horse_history",
                    severity="medium",
                    code="history_header_missing",
                    message=f"Missing expected history header: {required}",
                    context={"race_id": race_id, "horse_id": horse_id, "horse_name": horse_name},
                )

        body_rows = [tr for tr in table.select("tr") if tr.select("td")]
        out: list[dict[str, str]] = []

        for run_idx, tr in enumerate(body_rows[:5], start=1):
            raw_cells = [self._norm(td.get_text(" ", strip=True)) for td in tr.select("td")]
            cells = self._repair_cells(
                raw_cells,
                len(headers),
                header_matches,
                issue_sink,
                context={"race_id": race_id, "horse_id": horse_id, "run_index": str(run_idx)},
                aggressive=aggressive_repair,
            )
            if not any(cells):
                continue

            mapped = self._map_row(header_matches, cells)
            self._apply_last_3f_fallback(
                mapped,
                horse_name=horse_name,
                race_id=race_id,
                run_index=run_idx,
                issue_sink=issue_sink,
            )

            row = {
                "race_id": race_id,
                "horse_id": horse_id,
                "horse_name": horse_name,
                "horse_url": horse_url,
                "run_index": str(run_idx),
                **mapped,
            }
            if current_entry is not None:
                row.update(
                    {
                        "frame_number": current_entry.frame_number,
                        "horse_number": current_entry.horse_number,
                        "current_jockey": current_entry.current_jockey,
                        "assigned_weight": current_entry.assigned_weight,
                        "current_odds": current_entry.current_odds,
                        "current_popularity": current_entry.current_popularity,
                        "target_track": current_entry.target_track,
                        "target_race_date": current_entry.target_race_date,
                        "target_race_number": current_entry.target_race_number,
                        "target_surface": current_entry.target_surface,
                        "target_distance": current_entry.target_distance,
                        "horse_country": current_entry.horse_country,
                    }
                )
            out.append(row)

        return out

    def _parse_win_place_odds(
        self,
        soup: BeautifulSoup,
        *,
        race_id: str,
        source_cname: str,
        captured_at: str,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for table in soup.select("table"):
            if not table.select_one("td.odds_tan") and not table.select_one("td.odds_fuku"):
                continue
            for tr in table.select("tbody tr"):
                horse_number = self._cell_text(tr.select_one("td.num"))
                if not horse_number:
                    continue
                win_odds = self._normalize_decimal_like(self._cell_text(tr.select_one("td.odds_tan")))
                if win_odds:
                    rows.append(
                        _odds_row(
                            race_id=race_id,
                            bet_type="win",
                            combination=horse_number,
                            odds=win_odds,
                            captured_at=captured_at,
                            source_cname=source_cname,
                        )
                    )

                place_text = self._cell_text(tr.select_one("td.odds_fuku"))
                place_min, place_max = _parse_odds_range(place_text)
                if place_min:
                    rows.append(
                        _odds_row(
                            race_id=race_id,
                            bet_type="place",
                            combination=horse_number,
                            odds=place_min,
                            odds_min=place_min,
                            odds_max=place_max,
                            captured_at=captured_at,
                            source_cname=source_cname,
                        )
                    )
        return rows

    def _parse_matrix_odds(
        self,
        soup: BeautifulSoup,
        bet_type: str,
        table_class: str,
        race_id: str,
        source_cname: str,
        captured_at: str,
        *,
        ordered: bool = False,
        caption_pair: bool = False,
        odds_range: bool = False,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for table in soup.select(f"table.{table_class}"):
            caption_numbers = _number_tokens(table.select_one("caption").get_text(" ", strip=True) if table.select_one("caption") else "")
            if not caption_numbers:
                caption_class = " ".join(table.select_one("caption").get("class", [])) if table.select_one("caption") else ""
                caption_numbers = _number_tokens(caption_class)
            if not caption_numbers:
                continue

            for tr in table.select("tbody tr"):
                row_number = self._cell_text(tr.select_one("th"))
                row_numbers = _number_tokens(row_number)
                if not row_numbers:
                    continue
                odds_text = self._cell_text(tr.select_one("td"))
                odds_min, odds_max = _parse_odds_range(odds_text)
                if not odds_min:
                    continue

                numbers = caption_numbers + [row_numbers[0]]
                if caption_pair and len(numbers) != 3:
                    continue
                if not ordered:
                    numbers = sorted(numbers, key=lambda item: int(item))
                separator = ">" if ordered else "-"
                rows.append(
                    _odds_row(
                        race_id=race_id,
                        bet_type=bet_type,
                        combination=separator.join(numbers),
                        odds=odds_min,
                        odds_min=odds_min if odds_range else "",
                        odds_max=odds_max if odds_range else "",
                        captured_at=captured_at,
                        source_cname=source_cname,
                    )
                )
        return rows

    def _parse_trifecta_odds(
        self,
        soup: BeautifulSoup,
        *,
        race_id: str,
        source_cname: str,
        captured_at: str,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for table in soup.select("table.tan3"):
            parent = table.parent
            first_two: list[str] = []
            if parent is not None:
                for line in parent.select(":scope > div.p_line"):
                    number = self._cell_text(line.select_one(".num"))
                    if number:
                        first_two.append(number)
            if len(first_two) != 2:
                continue
            for tr in table.select("tbody tr"):
                third = self._cell_text(tr.select_one("th"))
                third_numbers = _number_tokens(third)
                if not third_numbers:
                    continue
                odds = self._normalize_decimal_like(self._cell_text(tr.select_one("td")))
                if not odds:
                    continue
                numbers = first_two + [third_numbers[0]]
                rows.append(
                    _odds_row(
                        race_id=race_id,
                        bet_type="sanrentan",
                        combination=">".join(numbers),
                        odds=odds,
                        captured_at=captured_at,
                        source_cname=source_cname,
                    )
                )
        return rows

    def _select_entry_table(self, soup: BeautifulSoup):
        best_table = None
        best_score = -1
        for table in soup.select("table"):
            score = 0
            headers = self._extract_headers(table)
            if headers:
                matches = self._build_header_matches(headers, self.ENTRY_HEADER_ALIASES)
                canonicals = {m.canonical for m in matches}
                if "horse_name" in canonicals:
                    score += 4
                if "horse_number" in canonicals:
                    score += 3
                if "frame_number" in canonicals:
                    score += 2
                if "current_jockey" in canonicals:
                    score += 1
                if "current_odds" in canonicals:
                    score += 1
            anchors = table.select("a[href]")
            if anchors:
                score += min(len(anchors), 5)
            if table.select_one("td.horse"):
                score += 3
            if score > best_score:
                best_score = score
                best_table = table
        return best_table

    def _select_last5_table(self, soup: BeautifulSoup):
        best_table = None
        best_score = -1

        for table in soup.select("table"):
            headers = self._extract_headers(table)
            if not headers:
                continue

            matches = self._build_header_matches(headers, self.HISTORY_HEADER_ALIASES)
            canonicals = {m.canonical for m in matches}
            score = 0

            if "position" in canonicals:
                score += 3
            if "race_name" in canonicals:
                score += 3
            if "distance" in canonicals:
                score += 2
            if "time" in canonicals:
                score += 2
            if "last_3f" in canonicals:
                score += 3
            if "popularity" in canonicals:
                score += 2
            if "date" in canonicals:
                score += 1
            if "jockey" in canonicals:
                score += 1
            if "weight" in canonicals:
                score += 1

            body_row_count = len([tr for tr in table.select("tr") if tr.select("td")])
            if body_row_count >= 5:
                score += 2

            if score > best_score:
                best_score = score
                best_table = table

        return best_table

    def _extract_headers(self, table) -> list[str]:
        header_rows = table.select("tr")
        if not header_rows:
            return []

        for tr in header_rows[:3]:
            cells = tr.select("th")
            if not cells:
                cells = tr.select("td")
            values = [self._norm(cell.get_text(" ", strip=True)) for cell in cells]
            non_empty = [v for v in values if v]
            if len(non_empty) >= 3:
                return values
        return []

    def _build_header_matches(
        self,
        headers: Sequence[str],
        aliases_map: dict[str, tuple[str, ...]],
    ) -> list[HeaderMatch]:
        matches: list[HeaderMatch] = []
        for idx, raw_header in enumerate(headers):
            canonical = self._canonicalize_header(raw_header, aliases_map)
            if canonical:
                matches.append(HeaderMatch(index=idx, canonical=canonical, raw=raw_header))
        return matches

    def _canonicalize_header(
        self,
        header: str,
        aliases_map: dict[str, tuple[str, ...]],
    ) -> str | None:
        normalized = self._normalize_header_label(header)
        for canonical, aliases in aliases_map.items():
            alias_norms = {self._normalize_header_label(a) for a in aliases}
            if normalized in alias_norms:
                return canonical
        return None

    def _repair_cells(
        self,
        cells: list[str],
        expected_count: int,
        matches: Sequence[HeaderMatch],
        issue_sink: list[ParserIssue] | None,
        *,
        context: dict[str, str],
        aggressive: bool,
    ) -> list[str]:
        if expected_count <= 0 or len(cells) == expected_count:
            return cells

        repaired = list(cells)
        if len(repaired) < expected_count:
            self._issue(
                issue_sink,
                stage="parser.repair",
                severity="medium",
                code="row_padding",
                message="Padded a short row to match header count.",
                context=context,
            )
            repaired.extend([""] * (expected_count - len(repaired)))
            return repaired

        horse_idx = next((m.index for m in matches if m.canonical == "horse_name"), None)
        current_jockey_idx = next((m.index for m in matches if m.canonical == "current_jockey"), None)
        merge_idx = horse_idx if horse_idx is not None else current_jockey_idx

        self._issue(
            issue_sink,
            stage="parser.repair",
            severity="medium",
            code="row_merge",
            message="Merged extra cells to repair a shifted row.",
            context={**context, "before_len": str(len(cells)), "after_len": str(expected_count)},
        )

        while len(repaired) > expected_count:
            if merge_idx is not None and merge_idx + 1 < len(repaired):
                repaired[merge_idx] = " ".join(part for part in (repaired[merge_idx], repaired.pop(merge_idx + 1)) if part).strip()
            elif aggressive and len(repaired) >= 2:
                repaired[-2] = " ".join(part for part in (repaired[-2], repaired.pop(-1)) if part).strip()
            else:
                repaired.pop()
        return repaired

    @staticmethod
    def _map_row(matches: Sequence[HeaderMatch], values: Sequence[str]) -> dict[str, str]:
        record: dict[str, str] = {}
        for match in matches:
            if match.index >= len(values):
                continue
            value = values[match.index]
            if value:
                record[match.canonical] = value
        return record

    def _apply_entry_fallbacks(
        self,
        mapped: dict[str, str],
        row,
        raw_cells: Sequence[str],
        aggressive_repair: bool,
        *,
        allow_odds_fallback: bool,
    ) -> dict[str, str]:
        out = dict(mapped)
        texts = [self._norm(text) for text in raw_cells if self._norm(text)]
        anchor = row.select_one("a[href]")
        if anchor and not out.get("horse_name"):
            out["horse_name"] = self._norm(anchor.get_text(" ", strip=True))
        if not out.get("horse_name"):
            horse_name_node = row.select_one("td.horse .name_line .name .txt")
            if horse_name_node is not None:
                out["horse_name"] = self._norm(horse_name_node.get_text(" ", strip=True))

        if not out.get("current_jockey"):
            jockey_node = row.select_one("td.jockey p.jockey")
            if jockey_node is not None:
                out["current_jockey"] = self._norm(jockey_node.get_text(" ", strip=True))

        if not out.get("assigned_weight"):
            weight_node = row.select_one("td.jockey p.weight")
            if weight_node is not None:
                weight_value = self._normalize_decimal_like(self._norm(weight_node.get_text(" ", strip=True)))
                if weight_value:
                    out["assigned_weight"] = weight_value

        if not out.get("horse_country"):
            country_node = row.select_one("td.jockey p.code")
            if country_node is not None:
                out["horse_country"] = self._normalize_country_code(
                    self._norm(country_node.get_text(" ", strip=True))
                )
        if not out.get("horse_country"):
            country_flag = row.select_one("td.horse .name_line .name .line .flag img")
            if country_flag is not None:
                out["horse_country"] = self._normalize_country_code(
                    self._norm(country_flag.get("alt") or country_flag.get("title") or "")
                )

        if not out.get("current_popularity"):
            popularity_node = row.select_one(".name_line .odds .pop_rank")
            if popularity_node is not None:
                popularity = self._normalize_int_like(self._norm(popularity_node.get_text(" ", strip=True)))
                if popularity:
                    out["current_popularity"] = popularity

        if not out.get("current_odds"):
            odds_node = row.select_one(".name_line .odds .num strong")
            if odds_node is not None:
                odds_value = self._normalize_decimal_like(self._norm(odds_node.get_text(" ", strip=True)))
                if odds_value:
                    out["current_odds"] = odds_value

        if not out.get("horse_number"):
            numbers = [self._normalize_int_like(value) for value in texts]
            horse_number = next((n for n in numbers if n and 1 <= int(n) <= 18), "")
            if horse_number:
                out["horse_number"] = horse_number

        if not out.get("frame_number"):
            numbers = [self._normalize_int_like(value) for value in texts]
            frame_number = next((n for n in numbers if n and 1 <= int(n) <= 8), "")
            if frame_number:
                out["frame_number"] = frame_number

        if not out.get("current_odds") and (allow_odds_fallback or aggressive_repair):
            odds_candidates = []
            for value in texts:
                normalized = self._normalize_decimal_like(value)
                if not normalized:
                    continue
                if normalized in {out.get("frame_number", ""), out.get("horse_number", ""), out.get("assigned_weight", "")}:
                    continue
                if "." not in value and float(normalized) < 2.0:
                    continue
                odds_candidates.append(normalized)
            odds = odds_candidates[0] if odds_candidates else ""
            if odds:
                out["current_odds"] = odds

        if not out.get("assigned_weight"):
            weight = next(
                (
                    self._normalize_decimal_like(value)
                    for value in texts
                    if self._normalize_decimal_like(value) and 45.0 <= float(self._normalize_decimal_like(value)) <= 65.0
                ),
                "",
            )
            if weight:
                out["assigned_weight"] = weight

        if aggressive_repair and not out.get("current_jockey"):
            for text in texts:
                if not re.search(r"\d", text) and len(text) <= 8 and text != out.get("horse_name", ""):
                    out["current_jockey"] = text
                    break

        return out

    def _apply_last_3f_fallback(
        self,
        row: dict[str, str],
        *,
        horse_name: str,
        race_id: str,
        run_index: int,
        issue_sink: list[ParserIssue] | None,
    ) -> None:
        if row.get("last_3f"):
            row["last_3f_source"] = row.get("last_3f_source", "observed")
            return
        row["last_3f"] = self.LAST_3F_NEUTRAL_BASELINE
        row["last_3f_source"] = "fallback"
        self._issue(
            issue_sink,
            stage="parser.horse_history",
            severity="medium",
            code="last3f_fallback",
            message="Applied neutral last_3f fallback because the source cell was missing.",
            context={"race_id": race_id, "horse_name": horse_name, "run_index": str(run_index)},
        )

    def _extract_race_conditions(self, soup: BeautifulSoup) -> tuple[str, str]:
        candidates = [
            self._norm(node.get_text(" ", strip=True))
            for node in soup.select(".race_header .cell.course, .race_title .cell.course, .cell.course")
        ]
        candidates.append(soup.get_text(" ", strip=True))

        for text in candidates:
            surface, distance = self._parse_condition_text(text)
            if surface and distance:
                return surface, distance
        return "", ""

    @classmethod
    def _parse_condition_text(cls, text: str) -> tuple[str, str]:
        normalized = cls._norm(text).replace(",", "")
        patterns = (
            r"(?P<surface>芝|ダート|ダ|障害)\s*(?P<distance>\d{3,4})\s*(?:m|メートル)?",
            r"(?P<distance>\d{3,4})\s*(?:m|メートル).*?(?P<surface>芝|ダート|ダ|障害)",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                return cls._normalize_surface(match.group("surface")), match.group("distance")
        return "", ""

    @classmethod
    def _extract_race_header_metadata(cls, soup: BeautifulSoup) -> dict[str, str]:
        date_text = cls._extract_first_text(soup, ".race_header .cell.date, .cell.date")
        race_name = cls._extract_first_text(soup, ".race_header .race_name, .race_title .race_name, .race_name")
        race_number_text = cls._extract_first_text(
            soup,
            ".race_header .race_number img[alt], .race_title .race_number img[alt], .nav.race-num .current img[alt]",
            attr="alt",
        )
        surface, distance = cls._parse_condition_text(
            cls._extract_first_text(soup, ".race_header .cell.course, .race_title .cell.course, .cell.course")
        )

        date = ""
        date_match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", date_text)
        if date_match:
            date = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"

        track = next((track_name for track_name in TRACKS if track_name in date_text), "")
        race_number = ""
        race_number_match = re.search(r"(\d{1,2})\s*レース", race_number_text)
        if race_number_match:
            race_number = f"{int(race_number_match.group(1)):02d}"

        return {
            "race_date": date,
            "track": track,
            "race_number": race_number,
            "race_name": race_name,
            "target_surface": surface,
            "target_distance": distance,
        }

    @staticmethod
    def _normalize_surface(surface: str) -> str:
        return "ダート" if surface == "ダ" else surface

    @staticmethod
    def _resolve_race_name(current: str, parsed: str) -> str:
        current = current.strip()
        parsed = parsed.strip()
        if parsed and (not current or current == "JRAレース" or current.startswith("direct_race_")):
            return parsed
        return current

    @staticmethod
    def _extract_first_text(soup: BeautifulSoup, selector: str, *, attr: str | None = None) -> str:
        node = soup.select_one(selector)
        if node is None:
            return ""
        if attr:
            return JRAParser._norm(node.get(attr) or "")
        return JRAParser._norm(node.get_text(" ", strip=True))

    @staticmethod
    def _parse_race_id_components(race_id: str) -> tuple[str, str, str]:
        match = re.match(r"(?P<date>\d{8})_(?P<track>.+)_(?P<number>\d{2})$", race_id)
        if not match:
            return "", "", ""
        raw_date = match.group("date")
        date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        return date, match.group("track"), match.group("number")

    @staticmethod
    def _build_race_id(href: str, race_name: str) -> str:
        date = ""
        match = re.search(r"(20\d{6})", href)
        if match:
            date = match.group(1)
        track = next((t for t in TRACKS if t in race_name), "unknown")
        race_match = re.search(r"(\d{1,2})R", race_name)
        race_no = f"{int(race_match.group(1)):02d}" if race_match else "00"
        if date:
            return f"{date}_{track}_{race_no}"
        digest = hashlib.md5((href + race_name).encode("utf-8")).hexdigest()[:10]
        return f"race_{digest}"

    def _extract_embedded_history(self, row) -> list[dict[str, str]]:
        histories: list[dict[str, str]] = []
        for run_idx, cell in enumerate(row.select("td.past"), start=1):
            date = self._extract_text(cell, ".date_line .date")
            race_name = self._extract_text(cell, ".race_line .name")
            if not date and not race_name:
                continue

            href_node = cell.select_one(".race_line .name a[href]")
            corner_values = [
                self._normalize_int_like(self._norm(li.get_text(" ", strip=True)))
                for li in cell.select(".corner_list li")
            ]
            last3f_text = self._extract_text(cell, ".info_line3 .f3")
            last3f = self._extract_last_3f_value(last3f_text)

            histories.append(
                {
                    "run_index": str(run_idx),
                    "date": date,
                    "course": self._extract_text(cell, ".date_line .rc"),
                    "race_name": race_name,
                    "distance": self._extract_text(cell, ".info_line2 .dist"),
                    "position": self._normalize_int_like(self._extract_text(cell, ".place_line .place")),
                    "time": self._extract_text(cell, ".info_line2 .time"),
                    "weight": self._normalize_decimal_like(self._extract_text(cell, ".info_line1 .weight")),
                    "jockey": self._extract_text(cell, ".info_line1 .jockey"),
                    "pace": "",
                    "last_3f": last3f,
                    "last_3f_source": "embedded" if last3f else "",
                    "track_condition": self._extract_text(cell, ".info_line2 .condition"),
                    "weather": "",
                    "passing_order": "-".join(value for value in corner_values if value),
                    "odds": "",
                    "popularity": self._normalize_int_like(self._extract_text(cell, ".place_line .pop")),
                    "race_result_url": urljoin(self.base_url, href_node.get("href")) if href_node else "",
                }
            )
        return histories

    @staticmethod
    def _extract_horse_id(href: str, horse_name: str) -> str:
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        cname = query.get("CNAME", [""])[0]
        if cname:
            return cname
        digits = "".join(ch for ch in href if ch.isdigit())
        if digits:
            return digits
        cleaned = re.sub(r"\s+", "_", horse_name.lower())
        return re.sub(r"[^a-z0-9_\-ぁ-んァ-ヶ一-龠]", "", cleaned) or "unknown_horse"

    @staticmethod
    def _dedupe_races(items: list[RaceLink]) -> list[RaceLink]:
        seen: set[str] = set()
        out: list[RaceLink] = []
        for item in items:
            if item.race_url in seen:
                continue
            seen.add(item.race_url)
            out.append(item)
        return out

    @staticmethod
    def _dedupe_horses(items: list[HorseEntry]) -> list[HorseEntry]:
        seen: set[tuple[str, str]] = set()
        out: list[HorseEntry] = []
        for item in items:
            key = (item.race_id, item.horse_id or item.horse_name)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    @staticmethod
    def _normalize_header_label(value: str) -> str:
        value = JRAParser._norm(value)
        value = value.replace(" ", "")
        value = value.replace("　", "")
        value = value.replace("Ｆ", "F").replace("３", "3").replace("４", "4")
        value = value.replace("ｆ", "F")
        return value

    @staticmethod
    def _normalize_int_like(value: str) -> str:
        match = re.search(r"\d+", value)
        return match.group(0) if match else ""

    @staticmethod
    def _normalize_decimal_like(value: str) -> str:
        match = re.search(r"\d+(?:\.\d+)?", value)
        return match.group(0) if match else ""

    @staticmethod
    def _normalize_country_code(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z]", "", value or "").upper()
        return cleaned[:3] if cleaned else ""

    @staticmethod
    def _extract_text(node, selector: str) -> str:
        child = node.select_one(selector)
        if child is None:
            return ""
        return JRAParser._norm(child.get_text(" ", strip=True))

    @staticmethod
    def _cell_text(node) -> str:
        if node is None:
            return ""
        return JRAParser._norm(node.get_text(" ", strip=True).replace("\xa0", " "))

    @staticmethod
    def _extract_last_3f_value(value: str) -> str:
        matches = re.findall(r"\d+(?:\.\d+)?", value)
        return matches[-1] if matches else ""

    @staticmethod
    def _norm(value: str) -> str:
        return " ".join(value.split())

    @staticmethod
    def _issue(
        issue_sink: list[ParserIssue] | None,
        *,
        stage: str,
        severity: str,
        code: str,
        message: str,
        context: dict[str, str],
    ) -> None:
        if issue_sink is None:
            return
        issue_sink.append(
            ParserIssue(
                stage=stage,
                severity=severity,
                code=code,
                message=message,
                context=context,
            )
        )


def _extract_access_o_cnames(html: str) -> list[str]:
    return [
        cname
        for onclick in re.findall(r"doAction\('/JRADB/accessO\.html',\s*'([^']+)'\)", html)
        if (cname := onclick.strip())
    ]


def _extract_cname_from_onclick(value: str) -> str:
    match = re.search(r"doAction\('/JRADB/accessO\.html',\s*'([^']+)'\)", value)
    return match.group(1).strip() if match else ""


def _number_tokens(value: str) -> list[str]:
    return [str(int(token)) for token in re.findall(r"\d+", value or "")]


def _parse_odds_range(value: str) -> tuple[str, str]:
    numbers = re.findall(r"\d+(?:\.\d+)?", value or "")
    if not numbers:
        return "", ""
    if len(numbers) == 1:
        return numbers[0], ""
    return numbers[0], numbers[1]


def _odds_row(
    *,
    race_id: str,
    bet_type: str,
    combination: str,
    odds: str,
    captured_at: str,
    source_cname: str,
    odds_min: str = "",
    odds_max: str = "",
) -> dict[str, str]:
    return {
        "race_id": race_id,
        "bet_type": bet_type,
        "combination": combination,
        "odds": odds,
        "odds_min": odds_min,
        "odds_max": odds_max,
        "captured_at": captured_at,
        "source_cname": source_cname,
    }
