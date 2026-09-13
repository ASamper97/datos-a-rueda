"""Analysis layer: data/*.parquet -> tables and charts in data/.

Reads only the parquet files written by parse.py: no HTML, no network.

The question: where did each rider come from when they joined their first
WorldTeam or ProTeam? "Where from" is the level of their last team before
that spell (trainee/stagiaire contracts don't count, neither as the WT/PRT
spell nor as the team before it).

Outputs:
- pathways.csv / pathways.png: riders per level of that previous team.
- development_teams.csv / development_teams.png: for riders who came straight
  from a development team, riders per development team (renames grouped).
- age_by_route.csv / age_by_route.png: age when joining the first WT/PRT, by
  route, with permutation tests printed to the console.
"""
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

# parse.py labels these as development teams only because their name carries
# a WorldTeam/ProTeam brand. The analysis doesn't count them as development
# teams; the value is the level they get instead, from their PCS class on the
# rider pages (CT -> CT, CLUB -> amateur).
NAME_ONLY_DEVELOPMENT = {
    "CTF Victorious": "CT",
    "Development Team Sunweb": "CT",
    "EF Education - Aevolo": "CT",
    "EOLO-Kometa U23": "amateur",
    "Hagens Berman Jayco": "CT",
}
TOP_LEVELS = {"WT", "PRT"}

# arrival level -> chart label
CATEGORIES = {
    "development": "From a development team",
    "CT": "From a Continental team",
    "amateur": "From an amateur team",
    "none": "No previous team listed",
}
HIGHLIGHT = "development"

# Development team (PCS name) -> sponsor line, so a renamed team counts once.
DEVELOPMENT_TEAM_SPONSOR = {
    "AG2R Citroën U23 Team": "Decathlon AG2R La Mondiale",
    "Decathlon AG2R La Mondiale Development Team": "Decathlon AG2R La Mondiale",
    "Alpecin-Deceuninck Development Team": "Alpecin-Deceuninck",
    "Arkéa - B&B Hôtels Continentale": "Arkéa-B&B Hotels",
    "Astana Qazaqstan Development Team": "Astana Qazaqstan",
    "Bahrain Victorious Development Team": "Bahrain Victorious",
    "Development Team DSM": "DSM / Picnic PostNL",
    "Development Team dsm-firmenich PostNL": "DSM / Picnic PostNL",
    "Development Team Picnic PostNL": "DSM / Picnic PostNL",
    "Equipe continentale Groupama-FDJ": "Groupama-FDJ",
    # Israel names stop in 2025 and NSN names start in 2026; three of the four
    # riders on Israel teams in 2025 are on NSN teams in 2026.
    "Israel Cycling Academy": "Israel-Premier Tech / NSN",
    "Israel Premier Tech Academy": "Israel-Premier Tech / NSN",
    "NSN Development Team": "Israel-Premier Tech / NSN",
    "Jumbo-Visma Development Team": "Visma | Lease a Bike",
    "Team Visma | Lease a Bike Development": "Visma | Lease a Bike",
    "Lidl - Trek Future Racing": "Lidl-Trek",
    "Lotto - Soudal U23": "Lotto",
    "Lotto Development Team": "Lotto",
    "Lotto Dstny Development Team": "Lotto",
    "Q36.5 Continental Team": "Q36.5",
    "Red Bull - BORA - hansgrohe Rookies": "Red Bull-BORA-hansgrohe",
    "Soudal Quick-Step Devo Team": "Soudal Quick-Step",
    "Tudor Pro Cycling Team U23": "Tudor",
    "UAE Team Emirates Gen Z": "UAE Team Emirates",
    "Uno-X Dare Development Team": "Uno-X Mobility",
    "Uno-X Mobility Development Team": "Uno-X Mobility",
}

# Chart style (square PNG for mobile), in pixel coordinates.
SIZE_PX, DPI = 1080, 180
MARGIN = 76
BACKGROUND = "#0E2436"
BAR_HIGHLIGHT = "#1D9E75"
BAR_MUTED = "#5E6E7B"
TEXT_PRIMARY = "#F2F5F7"
TEXT_SECONDARY = "#A9B6C0"
TEXT_MUTED = "#7F8E99"
FONT = ["Segoe UI", "Arial", "DejaVu Sans"]
# More bars than this and labels drop below ~11 px on a 390 px-wide phone.
MOBILE_MAX_BARS = 13
SEGMENT_GAP = 4  # surface gap between segments of a folded bar
GUIDE = "#1E3A4F"  # hairline guides, one step off the background

# Age by route. "none" (no previous team listed) is left out: one rider.
ROUTES = ["development", "CT", "amateur"]
HIGHLIGHT_AGE = 19
N_PERMUTATIONS = 20_000
RNG_SEED = 2026
ALPHA = 0.05


# --- tables ---------------------------------------------------------------------

def adjusted_teams(teams: pd.DataFrame) -> pd.DataFrame:
    teams = teams.copy()
    name_only = teams["team_name"].isin(NAME_ONLY_DEVELOPMENT)
    teams.loc[name_only, "team_level"] = teams.loc[name_only, "team_name"].map(NAME_ONLY_DEVELOPMENT)
    return teams


def arrival(spells: pd.DataFrame) -> dict:
    """First WT/PRT spell and the team right before it, for one rider.

    parse.py writes each rider's spells oldest first (year, then start date),
    so row order is chronological.
    """
    spells = spells.reset_index(drop=True)
    top = spells.index[spells["team_level"].isin(TOP_LEVELS) & ~spells["trainee"]]
    if top.empty:
        return {"first_top_team": None, "first_top_year": None, "first_top_level": None,
                "arrived_from_team": None, "arrived_from_level": None}
    first = spells.loc[top[0]]
    before = spells.iloc[: top[0]]
    before = before[~before["trainee"]]
    previous = before.iloc[-1] if len(before) else None
    return {
        "first_top_team": first["team_name"],
        "first_top_year": int(first["year"]),
        "first_top_level": first["team_level"],
        "arrived_from_team": None if previous is None else previous["team_name"],
        "arrived_from_level": "none" if previous is None else previous["team_level"],
    }


def pathways(riders: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"rider_id": rider_id, **arrival(spells)}
        for rider_id, spells in adjusted_teams(teams).groupby("rider_id", sort=False)
    ]
    table = riders[["rider_id", "name", "current_team"]].merge(
        pd.DataFrame(rows), on="rider_id", how="left", validate="one_to_one"
    )
    unknown = set(table["arrived_from_level"].dropna()) - CATEGORIES.keys()
    if unknown or table["first_top_team"].isna().any():
        raise SystemExit(f"unexpected data: levels {unknown}, "
                         f"riders without WT/PRT spell {table.loc[table.first_top_team.isna(), 'rider_id'].tolist()}")
    table["category"] = table["arrived_from_level"].map(CATEGORIES)
    return table.astype({"first_top_year": "Int64"})


def development_teams(pathways_table: pd.DataFrame) -> pd.DataFrame:
    """One row per sponsor line: riders who stepped up straight from it."""
    devo = pathways_table[pathways_table["arrived_from_level"] == "development"].copy()
    unmapped = sorted(set(devo["arrived_from_team"]) - DEVELOPMENT_TEAM_SPONSOR.keys())
    if unmapped:
        raise SystemExit(f"development teams without a sponsor line: {unmapped}")
    devo["sponsor"] = devo["arrived_from_team"].map(DEVELOPMENT_TEAM_SPONSOR)
    table = devo.groupby("sponsor").agg(
        riders=("rider_id", "size"),
        team_names=("arrived_from_team", lambda s: " + ".join(sorted(set(s)))),
        rider_names=("name", lambda s: ", ".join(sorted(s))),
    )
    return table.reset_index().sort_values(["riders", "sponsor"], ascending=[False, True],
                                           ignore_index=True)


def route_ages(pathways_table: pd.DataFrame, riders: pd.DataFrame) -> pd.DataFrame:
    """Riders on the compared routes with their age at the first WT/PRT.

    age_first_top (season minus birth year) is the age compared across
    routes. age_turned_pro from parse.py counts a Continental team as turning
    pro, so on the Continental route it dates the Continental spell, not the
    step up; it is kept in the table for reference only.
    """
    ages = pathways_table.merge(riders[["rider_id", "birth_date", "age_turned_pro"]],
                                on="rider_id", validate="one_to_one")
    ages["birth_year"] = pd.to_datetime(ages["birth_date"]).dt.year
    ages["age_first_top"] = ages["first_top_year"].astype(int) - ages["birth_year"]
    return ages[ages["arrived_from_level"].isin(ROUTES)].reset_index(drop=True)


def age_summary(ages: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for route in ROUTES:
        group = ages[ages["arrived_from_level"] == route]
        values = group["age_first_top"].to_numpy()
        boot = np.median(rng.choice(values, (N_PERMUTATIONS, values.size)), axis=1)
        rows.append({
            "route": CATEGORIES[route],
            "riders": values.size,
            "median_age_first_top": np.median(values),
            "min_age_first_top": values.min(),
            "max_age_first_top": values.max(),
            "median_95ci_low": np.percentile(boot, 2.5),
            "median_95ci_high": np.percentile(boot, 97.5),
            f"stepped_up_at_{HIGHLIGHT_AGE}": int((values == HIGHLIGHT_AGE).sum()),
            "median_age_turned_pro": group["age_turned_pro"].median(),
            "min_age_turned_pro": group["age_turned_pro"].min(),
            "max_age_turned_pro": group["age_turned_pro"].max(),
        })
    return pd.DataFrame(rows)


def _kruskal_h(ranks: np.ndarray, label_rows: np.ndarray) -> np.ndarray:
    """Kruskal-Wallis H (without tie correction) for each row of labels.

    The tie correction depends only on the pooled values, which permutations
    don't change, so it cancels out of a permutation p-value.
    """
    n = ranks.size
    h = np.zeros(label_rows.shape[0])
    for route in ROUTES:
        mask = label_rows == route
        size = mask.sum(axis=1)
        h += size * ((mask * ranks).sum(axis=1) / size - (n + 1) / 2) ** 2
    return 12 / (n * (n + 1)) * h


def _p_value(null: np.ndarray, observed: float) -> float:
    return (np.sum(null >= observed - 1e-9) + 1) / (null.size + 1)


def age_tests(ages: pd.DataFrame, rng: np.random.Generator) -> dict:
    """Permutation tests on age_first_top across routes."""
    values = ages["age_first_top"].to_numpy(dtype=float)
    labels = ages["arrived_from_level"].to_numpy()
    ranks = pd.Series(values).rank(method="average").to_numpy()
    observed = _kruskal_h(ranks, labels[None, :])[0]

    shuffled = labels[rng.random((N_PERMUTATIONS, labels.size)).argsort(axis=1)]
    # Same test with labels shuffled only among riders born the same year, so
    # a route can't look older just because its riders were born earlier.
    within_year = np.tile(labels, (N_PERMUTATIONS, 1))
    for year in np.unique(ages["birth_year"]):
        idx = np.flatnonzero(ages["birth_year"].to_numpy() == year)
        within_year[:, idx] = labels[idx[rng.random((N_PERMUTATIONS, idx.size)).argsort(axis=1)]]

    pairs = []
    for a, b in [("development", "CT"), ("development", "amateur"), ("CT", "amateur")]:
        x, y = values[labels == a], values[labels == b]
        pooled = np.concatenate([x, y])
        perm = pooled[rng.random((N_PERMUTATIONS, pooled.size)).argsort(axis=1)]
        null = np.abs(np.median(perm[:, :x.size], axis=1) - np.median(perm[:, x.size:], axis=1))
        gap = abs(np.median(x) - np.median(y))
        pairs.append({"routes": (a, b), "median_gap": gap, "p": _p_value(null, gap)})
    # Holm correction for the three pairwise comparisons.
    running = 0.0
    for rank, pair in enumerate(sorted(pairs, key=lambda d: d["p"])):
        running = max(running, min(1.0, (len(pairs) - rank) * pair["p"]))
        pair["p_holm"] = running

    return {
        "kruskal_h": observed,
        "p": _p_value(_kruskal_h(ranks, shuffled), observed),
        "p_within_birth_year": _p_value(_kruskal_h(ranks, within_year), observed),
        "pairs": pairs,
    }


# --- charts ---------------------------------------------------------------------

def draw_bar(ax, x: float, y: float, length: float, thickness: float, color: str,
             radius: float = 12) -> None:
    """Bar with a rounded data end and a square baseline.

    snap=False keeps the square part on the same sub-pixel edges as the
    rounded part; snapped, it leaves a 1 px step at the baseline.
    """
    radius = min(radius, length / 2)
    ax.add_patch(FancyBboxPatch((x, y), length, thickness, linewidth=0, facecolor=color,
                                boxstyle=f"round,pad=0,rounding_size={radius}", snap=False))
    ax.add_patch(Rectangle((x, y), radius, thickness, linewidth=0, facecolor=color, snap=False))


def right_edge(fig, ax, text) -> float:
    """x (pixels) where a text artist ends."""
    fig.canvas.draw()
    return ax.transData.inverted().transform(text.get_window_extent().get_points())[1][0]


def canvas(title: str, subtitle: str, note: str):
    """Square figure in pixel coordinates with the shared header and footer."""
    plt.rcParams["font.family"] = FONT
    fig = plt.figure(figsize=(SIZE_PX / DPI, SIZE_PX / DPI), dpi=DPI, facecolor=BACKGROUND)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, SIZE_PX)
    ax.set_ylim(SIZE_PX, 0)  # y grows downwards
    ax.axis("off")
    ax.text(MARGIN, 70, title, color=TEXT_PRIMARY, fontsize=21, fontweight="bold",
            va="top", linespacing=1.15)
    ax.text(MARGIN, 232, textwrap.fill(subtitle, 62), color=TEXT_SECONDARY,
            fontsize=11.5, va="top", linespacing=1.35)
    ax.text(MARGIN, 958, note, color=TEXT_MUTED, fontsize=8.5, va="top", linespacing=1.4)
    ax.text(MARGIN, 1024, "Source: ProCyclingStats", color=TEXT_MUTED, fontsize=8.5, va="top")
    ax.text(SIZE_PX - MARGIN, 1024, "@DatosARueda", color=TEXT_MUTED, fontsize=8.5,
            va="top", ha="right")
    return fig, ax


def save(fig, path: Path) -> None:
    fig.savefig(path, dpi=DPI, facecolor=BACKGROUND)
    plt.close(fig)


def pathways_chart(counts: pd.Series, total: int, path: Path) -> None:
    fig, ax = canvas(
        "How they reached their first\nWorldTeam or ProTeam",
        f"Last team before stepping up, for the {total} riders "
        "born 2001 or later who started the 2026 Vuelta",
        "Development team: U23 squad of a WorldTeam or ProTeam.\n"
        "Stagiaire contracts don't count as stepping up.",
    )
    # Room on the right for the value and percentage of the longest bar.
    bar_left, bar_max = MARGIN, SIZE_PX - 2 * MARGIN - 260
    block, thickness, top = 138, 58, 400
    for i, (level, n) in enumerate(counts.items()):
        y = top + i * block
        length = bar_max * n / counts.max()
        ax.text(bar_left, y, CATEGORIES[level], color=TEXT_PRIMARY, fontsize=12.5, va="top")
        bar_y = y + 44
        draw_bar(ax, bar_left, bar_y, length, thickness,
                 BAR_HIGHLIGHT if level == HIGHLIGHT else BAR_MUTED)
        value = ax.text(bar_left + length + 22, bar_y + thickness / 2, f"{n}",
                        color=TEXT_PRIMARY, fontsize=17, fontweight="bold", va="center")
        ax.text(right_edge(fig, ax, value) + 12, bar_y + thickness / 2, f"{100 * n / total:.0f}%",
                color=TEXT_SECONDARY, fontsize=12, va="center")
    save(fig, path)


def chart_bars(table: pd.DataFrame) -> list[tuple[str, list[int]]]:
    """(label, segments) per bar: one segment per team, sized in riders.

    If there are too many teams to read on a phone, the single-rider teams
    fold into one bar with a segment for each of them.
    """
    bars = [(sponsor, [n]) for sponsor, n in zip(table["sponsor"], table["riders"])]
    singles = [bar for bar in bars if bar[1] == [1]]
    if len(bars) <= MOBILE_MAX_BARS or len(singles) < 2:
        return bars
    kept = [bar for bar in bars if bar[1] != [1]]
    return kept + [(f"{len(singles)} teams with 1 rider each", [1] * len(singles))]


def development_teams_chart(table: pd.DataFrame, path: Path) -> None:
    """All bars gray: the story is how spread out the teams are, not a leader."""
    total = int(table["riders"].sum())
    fig, ax = canvas(
        f"{total} riders,\n{len(table)} different development teams",
        f"Where the {total} riders who stepped up from a development squad came from",
        "Renamed teams are grouped (e.g. Jumbo-Visma and Visma | Lease a Bike).\n"
        "Stagiaire contracts don't count as stepping up.",
    )
    bars = chart_bars(table)
    top, bottom = 352, 930
    row = (bottom - top) / len(bars)
    thickness = min(26, row * 0.62)
    fontsize = min(12.5, row / 3.7)
    labels = [
        ax.text(MARGIN, top + row * (i + 0.5), label, color=TEXT_PRIMARY, fontsize=fontsize,
                va="center")
        for i, (label, _) in enumerate(bars)
    ]
    bar_left = max(right_edge(fig, ax, label) for label in labels) + 26
    bar_max = SIZE_PX - MARGIN - bar_left - 60  # room for the value at the tip
    per_rider = bar_max / max(sum(segments) for _, segments in bars)
    for i, (_, segments) in enumerate(bars):
        center, x = top + row * (i + 0.5), bar_left
        for k, size in enumerate(segments):
            last = k == len(segments) - 1
            length = per_rider * size - (0 if last else SEGMENT_GAP)
            if last:
                draw_bar(ax, x, center - thickness / 2, length, thickness, BAR_MUTED, radius=8)
            else:
                ax.add_patch(Rectangle((x, center - thickness / 2), length, thickness,
                                       linewidth=0, facecolor=BAR_MUTED, snap=False))
            x += length + (0 if last else SEGMENT_GAP)
        ax.text(x + 16, center, f"{sum(segments)}", color=TEXT_PRIMARY, fontsize=fontsize + 1,
                fontweight="bold", va="center")
    save(fig, path)


def age_by_route_chart(ages: pd.DataFrame, summary: pd.DataFrame, tests: dict,
                       path: Path) -> None:
    """One dot per rider at their age, one row per route, median marked."""
    clear_gap = tests["p_within_birth_year"] < ALPHA and any(
        pair["p_holm"] < ALPHA for pair in tests["pairs"])
    highlighted = int((ages["age_first_top"] == HIGHLIGHT_AGE).sum())
    fig, ax = canvas(
        "Age at the step up differs\nby route" if clear_gap
        else "No clear age gap\nbetween the three routes",
        f"Age when riders joined their first WorldTeam or ProTeam. Each dot is a rider; "
        f"green marks the {highlighted} who stepped up at {HIGHLIGHT_AGE}",
        "Age = season minus birth year. One rider with no earlier team listed isn't shown.\n"
        "Small samples, and recent riders can only have stepped up young.",
    )
    age_min, age_max = int(ages["age_first_top"].min()), int(ages["age_first_top"].max())
    x_left, x_right = MARGIN + 60, SIZE_PX - MARGIN - 60
    step = (x_right - x_left) / max(age_max - age_min, 1)
    x_of = lambda age: x_left + (age - age_min) * step  # noqa: E731
    radius, pitch, per_line = 9, 24, 5
    top, block = 366, 172

    guides_top, guides_bottom = top + 40, top + block * (len(ROUTES) - 1) + 150
    for age in range(age_min, age_max + 1):
        ax.plot([x_of(age)] * 2, [guides_top, guides_bottom], color=GUIDE, linewidth=0.5,
                solid_capstyle="butt", zorder=0)
        ax.text(x_of(age), guides_bottom + 14, f"{age}", color=TEXT_MUTED, fontsize=11,
                ha="center", va="top")

    for i, route in enumerate(ROUTES):
        y = top + i * block
        stats = summary[summary["route"] == CATEGORIES[route]].iloc[0]
        ax.text(MARGIN, y, f"{CATEGORIES[route]}  ·  {stats.riders} riders  ·  "
                f"median {stats.median_age_first_top:g}",
                color=TEXT_PRIMARY, fontsize=12, va="top")
        dots_top = y + 62
        # median caret between the label and the dots
        mx = x_of(stats.median_age_first_top)
        ax.add_patch(plt.Polygon([(mx - 8, dots_top - 22), (mx + 8, dots_top - 22),
                                  (mx, dots_top - 10)], closed=True, facecolor=TEXT_PRIMARY,
                                 linewidth=0))
        counts = ages.loc[ages["arrived_from_level"] == route, "age_first_top"].value_counts()
        for age, n in counts.items():
            color = BAR_HIGHLIGHT if age == HIGHLIGHT_AGE else BAR_MUTED
            for k in range(n):
                line, col = divmod(k, per_line)
                in_line = min(per_line, n - line * per_line)
                cx = x_of(age) + (col - (in_line - 1) / 2) * pitch
                ax.add_patch(Circle((cx, dots_top + radius + line * pitch), radius,
                                    facecolor=color, linewidth=0))
    save(fig, path)


def main() -> None:
    riders = pd.read_parquet(DATA_DIR / "riders.parquet")
    teams = pd.read_parquet(DATA_DIR / "teams.parquet")

    table = pathways(riders, teams)
    table.to_csv(DATA_DIR / "pathways.csv", index=False, sep=";", encoding="utf-8-sig")
    counts = table["arrived_from_level"].value_counts().reindex(CATEGORIES.keys(), fill_value=0)
    counts = counts.sort_values(ascending=False, kind="stable")
    pathways_chart(counts, len(table), DATA_DIR / "pathways.png")
    print(f"{len(table)} riders -> data/pathways.csv, data/pathways.png")
    for level, n in counts.items():
        print(f"  {CATEGORIES[level]:30} {n:3}")

    devo = development_teams(table)
    devo.to_csv(DATA_DIR / "development_teams.csv", index=False, sep=";", encoding="utf-8-sig")
    development_teams_chart(devo, DATA_DIR / "development_teams.png")
    top_count = devo["riders"].max()
    if (devo["riders"] == top_count).sum() > 1:
        print(f"note: {(devo['riders'] == top_count).sum()} development teams tie at {top_count}")
    print(f"{devo['riders'].sum()} riders from {len(devo)} development teams "
          "-> data/development_teams.csv, data/development_teams.png")
    for row in devo.itertuples():
        print(f"  {row.sponsor:30} {row.riders:3}   {row.team_names}")

    rng = np.random.default_rng(RNG_SEED)
    ages = route_ages(table, riders)
    summary = age_summary(ages, rng)
    tests = age_tests(ages, rng)
    summary.to_csv(DATA_DIR / "age_by_route.csv", index=False, sep=";", encoding="utf-8-sig")
    age_by_route_chart(ages, summary, tests, DATA_DIR / "age_by_route.png")
    print(f"{len(ages)} riders by route -> data/age_by_route.csv, data/age_by_route.png")
    print(summary.drop(columns="route").set_index(summary["route"]).T.to_string())
    print(f"  Kruskal-Wallis H={tests['kruskal_h']:.2f}  permutation p={tests['p']:.3f}  "
          f"within birth year p={tests['p_within_birth_year']:.3f}")
    for pair in tests["pairs"]:
        a, b = pair["routes"]
        print(f"  {a} vs {b}: median gap {pair['median_gap']:g}  p={pair['p']:.3f}  "
              f"Holm p={pair['p_holm']:.3f}")


if __name__ == "__main__":
    main()
