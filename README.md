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
| `analyze.py` | Aggregates the parquet files into tables and charts | Never (not written yet) |

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; on macOS/Linux: source .venv/bin/activate
pip install procyclingstats requests beautifulsoup4 lxml pandas pyarrow matplotlib
python fetch.py riders --born-from 2001
python parse.py
```

`fetch.py` needs an identifying User-Agent in `user_agent.txt` (git-ignored), e.g. `Your Name (https://x.com/your_handle)`.

Definitions and decisions are documented in [`BRIEF.md`](BRIEF.md) (in Spanish) and at the top of `parse.py`.

## Data use rules

- **Rate limit:** at least 1.5 s between requests. A URL already in `cache/` is never requested again. Retries on 429/5xx use exponential backoff.
- **No Cloudflare evasion:** requests carry a real, identifying User-Agent. If PCS answers 403 or a Cloudflare challenge, `fetch.py` stops. The `procyclingstats` package is used only to parse cached HTML, never to download, because its fetcher spoofs a browser and can use `cloudscraper`.
- **PCS is the cited source** of every figure derived from this project.
- **No redistribution:** the downloaded pages (`cache/`) and the dataset (`data/`) are not published in this repository.
