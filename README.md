# datos-a-rueda

Cycling data analysis: pathways into the professional peloton.

For every rider on the 2026 Vuelta a España startlist born in 2001 or later, this project collects their team history, when and from where they turned pro (amateur team, WorldTeam/ProTeam development team, or other), how many UCI top-10s they had before turning pro, and whether they finished the race.

Data source: [ProCyclingStats](https://www.procyclingstats.com/).

## Pipeline

Three separate layers; each one only reads the output of the previous one.

| Layer | Does | Network |
|---|---|---|
| `fetch.py` | Downloads PCS pages into `cache/`, one file per URL | The only layer that uses it |
| `parse.py` | Reads `cache/` and writes `data/riders.parquet`, `data/teams.parquet`, `data/riders.csv` | Never |
| `analyze.py` | Reads the parquet files and writes the charts and tables described in [Analysis](#analysis) | Never |

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; on macOS/Linux: source .venv/bin/activate
pip install procyclingstats requests beautifulsoup4 lxml pandas pyarrow matplotlib
python fetch.py riders --born-from 2001
python parse.py
python analyze.py
```

`fetch.py` needs an identifying User-Agent in `user_agent.txt` (git-ignored), e.g. `Your Name (https://x.com/your_handle)`.

Definitions and decisions are documented in [`BRIEF.md`](BRIEF.md) (in Spanish) and at the top of `parse.py`.

## Analysis

`analyze.py` writes three square charts to `data/`, each with the CSV table behind it:

| Output | What it shows |
|---|---|
| `pathways.png` / `.csv` | How riders reached their first WorldTeam or ProTeam: from a development team, a Continental team, an amateur team, or with no previous team listed |
| `development_teams.png` / `.csv` | For riders who stepped up straight from a development team, how many came from each one (renamed teams grouped) |
| `age_by_route.png` / `.csv` | Age when joining the first WorldTeam or ProTeam, by route of entry, one dot per rider, with permutation tests on the differences |

All three outputs are reproduced by running `fetch.py`, `parse.py` and `analyze.py`, in that order, with PCS data as of the download date.

## Data use rules

- **Rate limit:** at least 1.5 s between requests. A URL already in `cache/` is never requested again. Retries on 429/5xx use exponential backoff.
- **No Cloudflare evasion:** requests carry a real, identifying User-Agent. If PCS answers 403 or a Cloudflare challenge, `fetch.py` stops. The `procyclingstats` package is used only to parse cached HTML, never to download, because its fetcher spoofs a browser and can use `cloudscraper`.
- **PCS is the cited source** of every figure derived from this project.
- **No redistribution:** the downloaded pages (`cache/`) and the dataset (`data/`) are not published in this repository.
