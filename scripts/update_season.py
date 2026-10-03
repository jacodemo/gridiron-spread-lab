from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from gridiron_spread.season_record import season_start_date, update_season_state
from gridiron_spread.sources import (
    calculate_elo_ratings,
    completed_elo_games,
    current_season_year,
    fetch_scoreboard_events,
    fetch_team_stats,
    fetch_weekly_matchups,
)
from scripts.build_site import SEASON_STATE_PATH, build_payload

CENTRAL = ZoneInfo("America/Chicago")


def _select_mode(requested_mode: str, now: datetime) -> str | None:
    if requested_mode != "scheduled":
        return requested_mode
    local_now = now.astimezone(CENTRAL)
    if local_now.weekday() in {2, 4} and local_now.hour == 8:
        return "refresh"
    if local_now.weekday() == 6 and local_now.hour == 23:
        return "grade"
    return None


def update_season(mode: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    selected_mode = _select_mode(mode, now)
    if selected_mode is None:
        print("This scheduled run is outside its America/Chicago execution window.")
        return False

    local_date = now.astimezone(CENTRAL).date()
    season = current_season_year(local_date)
    events = fetch_scoreboard_events(season_start_date(season), local_date)
    completed_games = completed_elo_games(events)
    elo_ratings = calculate_elo_ratings(completed_games)

    existing = (
        json.loads(SEASON_STATE_PATH.read_text(encoding="utf-8"))
        if SEASON_STATE_PATH.exists()
        else None
    )
    games: list[dict[str, object]] = []
    if selected_mode == "refresh":
        from gridiron_spread.model import ELO_POINTS_PER_RATING

        team_stats = fetch_team_stats(season)
        matchups = fetch_weekly_matchups(local_date, team_stats.keys())
        payload = build_payload(
            local_date,
            now,
            matchups,
            team_stats,
            elo_ratings,
        )
        games = payload["games"]
        print(
            f"Refreshed {len(games)} matchup projections and Elo ratings for "
            f"the week of {local_date.isoformat()} "
            f"({ELO_POINTS_PER_RATING} Elo points per spread point)."
        )

    state = update_season_state(
        existing,
        season,
        elo_ratings,
        games,
        completed_games,
        now,
        selected_mode,
    )
    SEASON_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEASON_STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Saved {len(state['recommendations'])} season recommendations; "
        f"mode={selected_mode}."
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh and grade the season bet record.")
    parser.add_argument(
        "--mode",
        choices=("scheduled", "refresh", "grade"),
        default="scheduled",
    )
    args = parser.parse_args()
    update_season(args.mode)


if __name__ == "__main__":
    main()
