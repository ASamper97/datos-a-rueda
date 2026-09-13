"""Parse layer: cached PCS HTML -> data/riders.parquet, data/teams.parquet.

Never touches the network: every page is read from cache/ (fetch.py decides
which file each URL maps to). Run fetch.py first.

Definitions (see BRIEF.md, "Decisiones cerradas"):
- Cohort: startlist riders born in BORN_FROM or later.
- team_level: WT / PRT / CT / amateur / development. Development teams are
  listed by name in DEVELOPMENT_TEAMS; teams without a PCS class count as
  amateur.
- Turning pro: first WT, PRT or CT spell that is not a development team and
  not a trainee (stagiaire) contract. Seasons after SEASON (signed future
  contracts) are ignored.
- top10s_before_pro: rank <= 10 in a UCI race (UCI_RESULT_CLASS) dated before
  the first pro spell starts. Stages, one-day races and general
  classifications count; points/mountains/youth classifications do not.
- age_2026 and age_turned_pro follow the UCI rule: year minus birth year.
"""
import argparse
import logging
import re
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from procyclingstats import RaceStartlist, Rider, RiderResults, Stage
from selectolax.parser import HTMLParser

from fetch import AGES_URL, SEASON, STARTLIST_URL, cache_path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOG_FILE = ROOT / "logs" / "parse.log"

BORN_FROM = 2001

# Development teams of WorldTeams/ProTeams, by exact PCS name (one entry per
# sponsor name). PCS registers most as CT and some U23 teams as CLUB, so the
# class alone can't separate them. The last block is classified only by its
# name carrying the brand of a WorldTeam/ProTeam: review it.
DEVELOPMENT_TEAMS = {
    "AG2R Citroën U23 Team",
    "Alpecin-Deceuninck Development Team",
    "Arkéa - B&B Hôtels Continentale",
    "Astana Qazaqstan Development Team",
    "Bahrain Victorious Development Team",
    "Decathlon AG2R La Mondiale Development Team",
    "Development Team DSM",
    "Development Team dsm-firmenich PostNL",
    "Development Team Picnic PostNL",
    "Equipe continentale Groupama-FDJ",
    "Israel Cycling Academy",
    "Israel Premier Tech Academy",
    "Jumbo-Visma Development Team",
    "Lidl - Trek Future Racing",
    "Lotto - Soudal U23",
    "Lotto Development Team",
    "Lotto Dstny Development Team",
    "NSN Development Team",
    "Q36.5 Continental Team",
    "Red Bull - BORA - hansgrohe Rookies",
    "Soudal Quick-Step Devo Team",
    "Team Visma | Lease a Bike Development",
    "Tudor Pro Cycling Team U23",
    "UAE Team Emirates Gen Z",
    "Uno-X Dare Development Team",
    "Uno-X Mobility Development Team",
    # classified by name only
    "CTF Victorious",
    "Development Team Sunweb",
    "EF Education - Aevolo",
    "EOLO-Kometa U23",
    "Hagens Berman Jayco",
}

# Team names that look like a development team but are not listed above are
# logged so the list can be reviewed.
DEV_NAME_HINT = re.compile(
    r"development|devo\b|rookies|academy|future racing|gen z|\bu23\b|continental", re.I
)

PCS_CLASS_TO_LEVEL = {"WT": "WT", "PRT": "PRT", "PCT": "PRT", "CT": "CT", "CLUB": "amateur"}
PRO_LEVELS = {"WT", "PRT", "CT"}

# Every 1.x / 2.x race plus world/continental/national championships and the
# Olympics. Left out: JR and JOJ.
UCI_RESULT_CLASS = re.compile(r"^[12]\.|^(WC|CC|NC|Olympics)$")

RIDER_COLUMNS = [
    "rider_id", "name", "nationality", "birth_date", "age_2026",
    "current_team", "first_pro_year", "first_pro_team", "age_turned_pro",
    "last_amateur_team", "last_amateur_year", "top10s_before_pro",
    "finished_race", "dropout_type", "dropout_stage",
    "passed_through_devo", "last_team_before_pro", "last_team_before_pro_level",
]
TEAM_COLUMNS = ["rider_id", "year", "team_name", "team_level", "trainee"]
INT_COLUMNS = [
    "age_2026", "first_pro_year", "age_turned_pro", "last_amateur_year",
    "top10s_before_pro", "dropout_stage",
]

log = logging.getLogger("parse")


def read_cached(url: str) -> str | None:
    path = cache_path(url)
    return path.read_text(encoding="utf-8") if path.exists() else None


def safe(rider_id: str, field: str, fn):
    """Run one field parser; None (logged) instead of an exception."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - any parser failure means "missing"
        log.debug("%s: %s failed: %r", rider_id, field, exc)
        return None


def clean(text: str) -> str:
    return " ".join(text.split())


# --- startlist and stage 1 ----------------------------------------------------

def parse_startlist(html: str) -> dict[str, dict]:
    """rider_url -> current team, nationality and dropout info."""
    riders = {
        row["rider_url"]: {
            "current_team": re.sub(r"\s*\([A-Z]+\)$", "", row["team_name"]).strip(),
            "nationality": row["nationality"],
            "finished_race": True,
            "dropout_type": None,
            "dropout_stage": None,
        }
        for row in RaceStartlist(STARTLIST_URL, html=html, update_html=False).startlist(
            "rider_url", "team_name", "nationality"
        )
    }
    # procyclingstats doesn't expose dropouts: <li class="dropout">... (DNF #8)</li>
    soup = BeautifulSoup(html, "lxml")
    for li in soup.select(".ridersCont li.dropout"):
        link = li.select_one('a[href^="rider/"]')
        if link is None or link["href"] not in riders:
            continue
        entry = riders[link["href"]]
        entry["finished_race"] = False
        match = re.search(r"\(([A-Z]+)\s*#\s*(\d+)\)", li.get_text(" ", strip=True))
        if match:
            entry["dropout_type"] = match.group(1)
            entry["dropout_stage"] = int(match.group(2))
        else:
            log.warning("%s: dropout without type/stage: %s", link["href"], li.get_text(" ", strip=True))
    return riders


def parse_stage_ages(html: str) -> dict[str, int]:
    rows = Stage(AGES_URL, html=html, update_html=False).results("rider_url", "age")
    return {row["rider_url"]: row["age"] for row in rows}


# --- rider page -----------------------------------------------------------------

def parse_birth_date(rider: Rider, html: str) -> tuple[date | None, str | None]:
    """(birth date, source). procyclingstats reads the date by position, which
    breaks on pages with an extra line (e.g. a name in another alphabet), so
    fall back to the "Date of birth:" line with BeautifulSoup."""
    try:
        year, month, day = map(int, rider.birthdate().split("-"))
        return date(year, month, day), "procyclingstats"
    except Exception:  # noqa: BLE001
        pass
    for li in BeautifulSoup(html, "lxml").select("li"):
        text = li.get_text(" ", strip=True)
        if not text.startswith("Date of birth"):
            continue
        match = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", text)
        if match:
            day, month_name, year = match.groups()
            month = datetime.strptime(month_name, "%B").month
            return date(int(year), month, int(day)), "beautifulsoup"
    return None, None


def _day_month(note: str, keyword: str) -> tuple[int, int] | None:
    match = re.search(rf"{keyword}\s+(\d{{1,2}})/(\d{{1,2}})", note)
    return (int(match.group(2)), int(match.group(1))) if match else None


def parse_team_history(html: str) -> list[dict]:
    """All team spells, oldest first.

    Own parser instead of Rider.teams_history(): that one drops teams without
    a PCS class and loses the "trainee" mark.
    """
    teams = []
    ul = HTMLParser(html).css_first("ul.rdr-teams2")
    if ul is None:
        return teams
    for li in ul.css("li.main"):
        season = li.css_first("div.season")
        name = li.css_first("div.name")
        if season is None or name is None or not season.text(strip=True).isdigit():
            continue
        anchor = name.css_first("a")
        note_node = name.css_first("span")
        note = note_node.text(strip=True) if note_node else ""
        pcs_class = re.search(r"</a>\s*\(([A-Z]+)\)", name.html or "")
        teams.append({
            "year": int(season.text(strip=True)),
            "team_name": clean(anchor.text() if anchor else name.text()),
            "pcs_class": pcs_class.group(1) if pcs_class else None,
            "since": _day_month(note, "as from") or (1, 1),
            "trainee": "trainee" in note.lower(),
        })
    teams.sort(key=lambda t: (t["year"], t["since"]))
    return teams


def team_level(team_name: str, pcs_class: str | None) -> str | None:
    if team_name in DEVELOPMENT_TEAMS:
        return "development"
    if pcs_class is None:
        return "amateur"
    return PCS_CLASS_TO_LEVEL.get(pcs_class)


def career_path(teams: list[dict]) -> dict:
    """Turning-pro fields from team spells (already levelled, <= SEASON)."""
    out = {
        "first_pro_year": None, "first_pro_team": None, "pro_start": None,
        "last_amateur_team": None, "last_amateur_year": None,
        "last_team_before_pro": None, "last_team_before_pro_level": None,
        "passed_through_devo": any(t["team_level"] == "development" for t in teams),
    }
    first_pro = next(
        (t for t in teams if t["team_level"] in PRO_LEVELS and not t["trainee"]), None
    )
    if first_pro is None:
        return out
    start_key = (first_pro["year"], first_pro["since"])
    before = [t for t in teams if (t["year"], t["since"]) < start_key and not t["trainee"]]
    amateur = [t for t in before if t["team_level"] == "amateur"]
    out.update(
        first_pro_year=first_pro["year"],
        first_pro_team=first_pro["team_name"],
        pro_start=date(first_pro["year"], *first_pro["since"]),
    )
    if before:
        out.update(last_team_before_pro=before[-1]["team_name"],
                   last_team_before_pro_level=before[-1]["team_level"])
    if amateur:
        out.update(last_amateur_team=amateur[-1]["team_name"],
                   last_amateur_year=amateur[-1]["year"])
    return out


# --- results --------------------------------------------------------------------

def is_secondary_classification(stage_name: str) -> bool:
    if "|" not in stage_name:
        return False
    part = stage_name.rsplit("|", 1)[1].strip().lower()
    return part.endswith("classification") and not part.startswith("general")


def results_page_urls(first_page_html: str) -> list[str]:
    """Same URLs as fetch.results_page_urls, read with selectolax: doing it
    with BeautifulSoup took ~15 s of a full run."""
    tree = HTMLParser(first_page_html)
    rider_id = tree.css_first('form[action="rider.php"] input[name="id"]')
    if rider_id is None:
        return []
    return [
        f"rider.php?id={rider_id.attributes['value']}&p=results&offset={offset}"
        for option in tree.css('select[name="offset"] option')
        if (offset := option.attributes.get("value")) and offset != "0"
    ]


def parse_results(rider_url: str) -> list[dict] | None:
    """Every results row across all cached pages; None if a page is missing."""
    first_url = f"{rider_url}/results"
    first = read_cached(first_url)
    if first is None:
        return None
    rows = []
    for url in [first_url] + results_page_urls(first):
        html = first if url == first_url else read_cached(url)
        if html is None:
            log.warning("%s: results page not in cache: %s", rider_url, url)
            return None
        rows += RiderResults(url, html=html, update_html=False).results(
            "date", "rank", "class", "stage_name"
        )
    return rows


def count_top10s_before(rows: list[dict], pro_start: date) -> int:
    return sum(
        1
        for row in rows
        if row["date"]
        and date.fromisoformat(row["date"]) < pro_start
        and isinstance(row["rank"], int) and row["rank"] <= 10
        and UCI_RESULT_CLASS.match(row["class"] or "")
        and not is_secondary_classification(row["stage_name"] or "")
    )


# --- main -----------------------------------------------------------------------

def setup_logging() -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)
    log.addHandler(file_handler)
    log.addHandler(console)
    log.setLevel(logging.DEBUG)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--born-from", type=int, default=BORN_FROM)
    args = parser.parse_args()
    setup_logging()
    started = time.perf_counter()

    startlist_html = read_cached(STARTLIST_URL)
    ages_html = read_cached(AGES_URL)
    if startlist_html is None or ages_html is None:
        raise SystemExit("startlist or stage 1 page not in cache: run fetch.py first")
    startlist = parse_startlist(startlist_html)
    ages = parse_stage_ages(ages_html)
    snapshot = datetime.fromtimestamp(cache_path(STARTLIST_URL).stat().st_mtime)
    log.info("startlist: %d riders, %d dropouts, cached %s",
             len(startlist), sum(not r["finished_race"] for r in startlist.values()),
             snapshot.isoformat(timespec="minutes"))

    riders, teams, lost, fallbacks, unreviewed = [], [], [], [], Counter()
    for rider_url, entry in startlist.items():
        rider_id = rider_url.split("/", 1)[1]
        age = ages.get(rider_url)
        html = read_cached(rider_url)
        if html is None:
            if age is None or age <= SEASON - args.born_from:
                lost.append((rider_id, "rider page not in cache"))
            continue
        rider = Rider(rider_url, html=html, update_html=False)
        birth, source = parse_birth_date(rider, html)
        if birth is None:
            if age is None or age <= SEASON - args.born_from:
                lost.append((rider_id, "no birth date"))
            continue
        if birth.year < args.born_from:
            continue
        if source != "procyclingstats":
            fallbacks.append(rider_id)

        spells = []
        for t in parse_team_history(html):
            if t["year"] > SEASON:
                continue
            t["team_level"] = team_level(t["team_name"], t["pcs_class"])
            if t["team_level"] is None:
                log.warning("%s: unknown PCS class %r for %s", rider_id, t["pcs_class"], t["team_name"])
            if t["team_level"] != "development" and DEV_NAME_HINT.search(t["team_name"]):
                unreviewed[t["team_name"]] += 1
            spells.append(t)
            teams.append({"rider_id": rider_id, **{k: t[k] for k in TEAM_COLUMNS[1:]}})
        path = career_path(spells)

        top10s = None
        if path["pro_start"] is not None:
            results = parse_results(rider_url)
            if results is not None:
                top10s = count_top10s_before(results, path["pro_start"])

        riders.append({
            "rider_id": rider_id,
            "name": safe(rider_id, "name", rider.name),
            "nationality": safe(rider_id, "nationality", rider.nationality) or entry["nationality"],
            "birth_date": birth,
            "age_2026": SEASON - birth.year,
            "current_team": entry["current_team"],
            "age_turned_pro": path["first_pro_year"] - birth.year if path["first_pro_year"] else None,
            "top10s_before_pro": top10s,
            "finished_race": entry["finished_race"],
            "dropout_type": entry["dropout_type"],
            "dropout_stage": entry["dropout_stage"],
            **{k: v for k, v in path.items() if k != "pro_start"},
        })

    riders_df = pd.DataFrame(riders, columns=RIDER_COLUMNS).astype({c: "Int64" for c in INT_COLUMNS})
    teams_df = pd.DataFrame(teams, columns=TEAM_COLUMNS)
    DATA_DIR.mkdir(exist_ok=True)
    riders_df.to_parquet(DATA_DIR / "riders.parquet", index=False)
    teams_df.to_parquet(DATA_DIR / "teams.parquet", index=False)
    # Semicolon + BOM so a Spanish-locale Excel opens it with accents intact.
    riders_df.to_csv(DATA_DIR / "riders.csv", index=False, sep=";", encoding="utf-8-sig")

    report(riders_df, lost, fallbacks, unreviewed)
    log.info("wrote %d riders, %d team spells in %.1fs",
             len(riders_df), len(teams_df), time.perf_counter() - started)


def report(riders_df: pd.DataFrame, lost, fallbacks, unreviewed: Counter) -> None:
    log.info("riders lost before the birth-year filter: %d %s", len(lost), lost)
    log.info("birth date via BeautifulSoup fallback: %d %s", len(fallbacks), fallbacks)
    incomplete = {}
    for row in riders_df.to_dict("records"):
        skip = {"dropout_type", "dropout_stage"} if row["finished_race"] else set()
        missing = [c for c in RIDER_COLUMNS if c not in skip and pd.isna(row[c])]
        if missing:
            incomplete[row["rider_id"]] = missing
    log.info("riders with missing fields: %d of %d", len(incomplete), len(riders_df))
    for field, n in Counter(f for fields in incomplete.values() for f in fields).most_common():
        log.info("  %-28s %d", field, n)
    for rider_id, fields in incomplete.items():
        log.info("  %s: %s", rider_id, ", ".join(fields))
    if unreviewed:
        log.warning("team names that look like development teams but aren't listed: %s",
                    dict(unreviewed))


if __name__ == "__main__":
    main()
