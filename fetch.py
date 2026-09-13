"""Download layer: the only code in this project that touches the network.

Every URL is stored as cache/{sha256(url)}.html. A URL that is already in the
cache is never requested again.

User-Agent: set PCS_USER_AGENT or write it to user_agent.txt (git-ignored).
"""
import argparse
import hashlib
import logging
import os
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://www.procyclingstats.com/"
ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"
LOG_FILE = ROOT / "logs" / "fetch.log"
UA_FILE = ROOT / "user_agent.txt"

SEASON = 2026
STARTLIST_URL = "race/vuelta-a-espana/2026/startlist"
# Stage 1 was an ITT ridden by the whole startlist; its table has every age.
AGES_URL = "race/vuelta-a-espana/2026/stage-1"

MIN_INTERVAL = 1.5  # seconds between real requests
MAX_ATTEMPTS = 5
BACKOFF_BASE = 5  # seconds; doubles on every retry

log = logging.getLogger("fetch")


class BlockedError(RuntimeError):
    """PCS/Cloudflare refused us. Stop instead of insisting."""


def absolute_url(url: str) -> str:
    return url if url.startswith("http") else BASE_URL + url.lstrip("/")


def url_hash(url: str) -> str:
    return hashlib.sha256(absolute_url(url).encode("utf-8")).hexdigest()


def cache_path(url: str) -> Path:
    return CACHE_DIR / f"{url_hash(url)}.html"


def missing_path(url: str) -> Path:
    """Marker for a URL that returned 404, so it is not requested again."""
    return CACHE_DIR / f"{url_hash(url)}.404"


def load_user_agent() -> str:
    ua = os.environ.get("PCS_USER_AGENT", "").strip()
    if not ua and UA_FILE.exists():
        ua = UA_FILE.read_text(encoding="utf-8").strip()
    if not ua or "[" in ua or "]" in ua:
        sys.exit(
            "User-Agent missing or still a template. Put your real one in "
            "user_agent.txt, e.g.: Your Name (https://x.com/your_handle)"
        )
    return ua


class Fetcher:
    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request = 0.0
        self.downloaded = 0
        self.from_cache = 0

    def get(self, url: str) -> Path | None:
        """Return the cached file for url, downloading it only if needed.

        Returns None for URLs that PCS answers with 404.
        """
        url = absolute_url(url)
        path = cache_path(url)
        if path.exists():
            self.from_cache += 1
            return path
        if missing_path(url).exists():
            self.from_cache += 1
            return None

        for attempt in range(MAX_ATTEMPTS):
            self._respect_interval()
            try:
                resp = self.session.get(url, timeout=30)
            except requests.RequestException as exc:
                self._last_request = time.monotonic()
                log.warning("network error on %s: %s", url, exc)
                self._backoff(attempt, None)
                continue
            self._last_request = time.monotonic()
            self.downloaded += 1
            log.info("%d %s (%d bytes)", resp.status_code, url, len(resp.content))

            if resp.status_code == 403 or resp.headers.get("cf-mitigated") == "challenge" \
                    or b"<title>Just a moment" in resp.content[:2000]:
                raise BlockedError(f"blocked on {url} (status {resp.status_code})")
            if resp.status_code == 200:
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(resp.content)
                tmp.replace(path)
                return path
            if resp.status_code == 404:
                missing_path(url).touch()
                return None
            if resp.status_code == 429 or resp.status_code >= 500:
                self._backoff(attempt, resp.headers.get("Retry-After"))
                continue
            raise RuntimeError(f"unexpected status {resp.status_code} on {url}")

        raise RuntimeError(f"giving up on {url} after {MAX_ATTEMPTS} attempts")

    def _respect_interval(self) -> None:
        wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> None:
        delay = BACKOFF_BASE * 2 ** attempt
        if retry_after and retry_after.isdigit():
            delay = max(delay, int(retry_after))
        log.warning("retrying in %ds (attempt %d/%d)", delay, attempt + 1, MAX_ATTEMPTS)
        time.sleep(delay)


def read_cached(fetcher: Fetcher, url: str) -> str:
    path = fetcher.get(url)
    if path is None:
        raise RuntimeError(f"{url} returned 404")
    return path.read_text(encoding="utf-8")


def candidate_riders(fetcher: Fetcher, born_from: int) -> list[str]:
    """Startlist riders who may be born in `born_from` or later, in bib order.

    This is the only parsing fetch.py does: just enough to know what to
    download. Someone born on or after 1 January of `born_from` is at most
    SEASON - born_from years old during SEASON, so filtering on the integer
    ages of the stage 1 table never drops a rider. parse.py applies the exact
    birth-year filter from each rider's own page.
    """
    from procyclingstats import RaceStartlist, Stage

    startlist = RaceStartlist(
        STARTLIST_URL, html=read_cached(fetcher, STARTLIST_URL), update_html=False
    ).startlist("rider_url")
    ages = {
        row["rider_url"]: row["age"]
        for row in Stage(
            AGES_URL, html=read_cached(fetcher, AGES_URL), update_html=False
        ).results("rider_url", "age")
    }
    max_age = SEASON - born_from
    return [
        row["rider_url"]
        for row in startlist
        if ages.get(row["rider_url"]) is None or ages[row["rider_url"]] <= max_age
    ]


def results_page_urls(first_page_html: str) -> list[str]:
    """URLs of results pages 2..n, read from the first page's page selector.

    PCS shows 100 results per page, newest first, so the pre-pro years are on
    the last pages. The site's own form pages through rider.php with the
    rider's numeric id and an offset.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(first_page_html, "lxml")
    rider_id = soup.select_one('form[action="rider.php"] input[name="id"]')
    offsets = [o.get("value") for o in soup.select('select[name="offset"] option')]
    if rider_id is None:
        return []
    return [
        f"rider.php?id={rider_id['value']}&p=results&offset={offset}"
        for offset in offsets
        if offset and offset != "0"
    ]


def fetch_riders(fetcher: Fetcher, born_from: int, limit: int | None) -> None:
    riders = candidate_riders(fetcher, born_from)
    log.info("%d candidate riders born >= %d (by stage 1 age)", len(riders), born_from)
    for rider_url in riders[:limit]:
        if fetcher.get(rider_url) is None:
            log.warning("404 %s", absolute_url(rider_url))
            continue
        results = fetcher.get(f"{rider_url}/results")
        if results is None:
            log.warning("404 %s/results", absolute_url(rider_url))
            continue
        for url in results_page_urls(results.read_text(encoding="utf-8")):
            fetcher.get(url)


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(file_handler)
    log.addHandler(console)
    log.setLevel(logging.INFO)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("url", help="fetch a single URL")
    one.add_argument("url")
    riders = sub.add_parser("riders", help="fetch startlist riders' pages and results")
    riders.add_argument("--born-from", type=int, default=2003)
    riders.add_argument("--limit", type=int, help="only the first N candidates")
    args = parser.parse_args()

    setup_logging()
    CACHE_DIR.mkdir(exist_ok=True)
    fetcher = Fetcher(load_user_agent())
    try:
        if args.command == "url":
            print(fetcher.get(args.url))
        elif args.command == "riders":
            fetch_riders(fetcher, args.born_from, args.limit)
    except BlockedError as exc:
        log.error("%s -- stopping", exc)
        sys.exit(2)
    finally:
        log.info("done: %d downloaded, %d from cache", fetcher.downloaded, fetcher.from_cache)


if __name__ == "__main__":
    main()
