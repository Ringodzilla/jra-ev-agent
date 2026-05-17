from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry

from .config import ScrapeConfig


logger = logging.getLogger(__name__)


class JRAScraper:
    """HTTP client with persistent raw cache and graceful fallback."""

    def __init__(self, config: ScrapeConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.memory_cache: dict[str, str] = {}

        retry = Retry(
            total=config.max_retries,
            connect=config.max_retries,
            read=config.max_retries,
            backoff_factor=1.0,
            status_forcelist=(408, 429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                )
            }
        )

    def _decode_japanese_html(self, response: requests.Response) -> str:
        for enc in ("euc_jp", "shift_jis"):
            try:
                return response.content.decode(enc)
            except UnicodeDecodeError:
                continue
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    def fetch(
        self,
        url: str,
        raw_name: Optional[str] = None,
        *,
        use_cache: bool = True,
        cache_only: bool = False,
    ) -> Optional[str]:
        """Fetch URL with memory+disk cache. cache_only=True avoids network requests."""
        cache_path = self._resolve_raw_path(url, raw_name)

        if use_cache and url in self.memory_cache:
            return self.memory_cache[url]
        if use_cache and cache_path.exists():
            html = cache_path.read_text(encoding="utf-8")
            self.memory_cache[url] = html
            logger.info("Cache hit: %s", url)
            return html
        if cache_only:
            logger.warning("Cache-only mode miss: %s", url)
            return None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.config.timeout)
                response.raise_for_status()
                html = self._decode_japanese_html(response)
                cache_path.write_text(html, encoding="utf-8")
                self.memory_cache[url] = html
                logger.info("Fetched %s", url)
                time.sleep(self.config.delay_seconds)
                return html
            except RequestException as exc:
                logger.warning(
                    "Fetch failed (attempt %d/%d) for %s: %s",
                    attempt,
                    self.config.max_retries,
                    url,
                    exc,
                )
                time.sleep(self.config.delay_seconds)

        logger.error("Giving up fetch after retries: %s", url)
        return None

    def fetch_relative(
        self,
        path: str,
        raw_name: Optional[str] = None,
        *,
        use_cache: bool = True,
        cache_only: bool = False,
    ) -> Optional[str]:
        return self.fetch(urljoin(self.config.base_url, path), raw_name, use_cache=use_cache, cache_only=cache_only)

    def fetch_post(
        self,
        url: str,
        data: Mapping[str, str],
        raw_name: Optional[str] = None,
        *,
        use_cache: bool = True,
        cache_only: bool = False,
    ) -> Optional[str]:
        """POST helper for JRADB pages that are normally opened via doAction()."""
        cache_key = f"POST {url} {sorted(data.items())}"
        cache_path = self._resolve_raw_path(cache_key, raw_name)

        if use_cache and cache_key in self.memory_cache:
            return self.memory_cache[cache_key]
        if use_cache and cache_path.exists():
            html = cache_path.read_text(encoding="utf-8")
            self.memory_cache[cache_key] = html
            logger.info("Cache hit: %s", cache_key)
            return html
        if cache_only:
            logger.warning("Cache-only mode miss: %s", cache_key)
            return None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.session.post(url, data=dict(data), timeout=self.config.timeout)
                response.raise_for_status()
                html = self._decode_japanese_html(response)
                cache_path.write_text(html, encoding="utf-8")
                self.memory_cache[cache_key] = html
                logger.info("Fetched %s", cache_key)
                time.sleep(self.config.delay_seconds)
                return html
            except RequestException as exc:
                logger.warning(
                    "POST fetch failed (attempt %d/%d) for %s: %s",
                    attempt,
                    self.config.max_retries,
                    cache_key,
                    exc,
                )
                time.sleep(self.config.delay_seconds)

        logger.error("Giving up POST fetch after retries: %s", cache_key)
        return None

    def _resolve_raw_path(self, url: str, raw_name: Optional[str]) -> Path:
        if raw_name:
            return self.config.raw_dir / raw_name
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        return self.config.raw_dir / f"url_{digest}.html"

    def close(self) -> None:
        self.session.close()


def safe_filename(name: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    return sanitized.strip("_") or "page"
