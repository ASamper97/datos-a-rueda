"""Analysis layer: data/*.parquet -> data/pathways.csv and data/pathways.png.

Reads only the parquet files written by parse.py: no HTML, no network.

The question: where did each rider come from when they joined their first
WorldTeam or ProTeam? "Where from" is the level of their last team before
that spell (trainee/stagiaire contracts don't count, neither as the WT/PRT
spell nor as the team before it).
"""
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

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

# Chart style (square PNG for mobile).
SIZE_PX, DPI = 1080, 180
BACKGROUND = "#0E2436"
BAR_HIGHLIGHT = "#1D9E75"
BAR_MUTED = "#5E6E7B"
TEXT_PRIMARY = "#F2F5F7"
TEXT_SECONDARY = "#A9B6C0"
TEXT_MUTED = "#7F8E99"
FONT = ["Segoe UI", "Arial", "DejaVu Sans"]


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


def draw_bar(ax, x: float, y: float, length: float, thickness: float, color: str) -> None:
    """Bar with a rounded data end and a square baseline."""
    radius = min(12, length / 2)
    ax.add_patch(FancyBboxPatch((x, y), length, thickness, linewidth=0, facecolor=color,
                                boxstyle=f"round,pad=0,rounding_size={radius}"))
    ax.add_patch(Rectangle((x, y), radius, thickness, linewidth=0, facecolor=color))


def chart(counts: pd.Series, total: int, path: Path) -> None:
    plt.rcParams["font.family"] = FONT
    fig = plt.figure(figsize=(SIZE_PX / DPI, SIZE_PX / DPI), dpi=DPI, facecolor=BACKGROUND)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, SIZE_PX)
    ax.set_ylim(SIZE_PX, 0)  # pixel coordinates, y grows downwards
    ax.axis("off")

    margin = 76
    ax.text(margin, 70, "How they reached their first\nWorldTeam or ProTeam", color=TEXT_PRIMARY,
            fontsize=21, fontweight="bold", va="top", linespacing=1.15)
    subtitle = (f"Last team before stepping up, for the {total} riders "
                "born 2001 or later who started the 2026 Vuelta")
    ax.text(margin, 232, textwrap.fill(subtitle, 62), color=TEXT_SECONDARY,
            fontsize=11.5, va="top", linespacing=1.35)

    # Room on the right for the value and percentage of the longest bar.
    bar_left, bar_max = margin, SIZE_PX - 2 * margin - 260
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
        fig.canvas.draw()
        value_right = ax.transData.inverted().transform(
            value.get_window_extent().get_points())[1][0]
        ax.text(value_right + 12, bar_y + thickness / 2, f"{100 * n / total:.0f}%",
                color=TEXT_SECONDARY, fontsize=12, va="center")

    note = ("Development team: U23 squad of a WorldTeam or ProTeam.\n"
            "Stagiaire contracts don't count as stepping up.")
    ax.text(margin, 958, note, color=TEXT_MUTED, fontsize=8.5, va="top", linespacing=1.4)
    ax.text(margin, 1024, "Source: ProCyclingStats", color=TEXT_MUTED, fontsize=8.5, va="top")
    ax.text(SIZE_PX - margin, 1024, "@DatosARueda", color=TEXT_MUTED, fontsize=8.5,
            va="top", ha="right")

    fig.savefig(path, dpi=DPI, facecolor=BACKGROUND)
    plt.close(fig)


def main() -> None:
    riders = pd.read_parquet(DATA_DIR / "riders.parquet")
    teams = pd.read_parquet(DATA_DIR / "teams.parquet")

    table = pathways(riders, teams)
    table.to_csv(DATA_DIR / "pathways.csv", index=False, sep=";", encoding="utf-8-sig")

    counts = table["arrived_from_level"].value_counts().reindex(CATEGORIES.keys(), fill_value=0)
    counts = counts.sort_values(ascending=False, kind="stable")
    chart(counts, len(table), DATA_DIR / "pathways.png")

    print(f"{len(table)} riders -> data/pathways.csv, data/pathways.png")
    for level, n in counts.items():
        print(f"  {CATEGORIES[level]:30} {n:3}")


if __name__ == "__main__":
    main()
