import argparse
import json
from datetime import date
from pathlib import Path

from .data import Matchup, team_key
from .model import DEFAULT_MODEL_PARAMETERS, project_matchup
from .sources import (
    current_season_year,
    fetch_season_elo_ratings,
    fetch_team_stats,
    fetch_weekly_matchups,
)

SEASON_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "season_record.json"


def _market_line(market_home_margin: float | None, sportsbook: str | None) -> str:
    if market_home_margin is None:
        return "unavailable"
    line = -market_home_margin
    return f"{sportsbook} home {line:+.1f}"


def _saved_model_parameters(season: int) -> dict[str, float]:
    if not SEASON_STATE_PATH.exists():
        return DEFAULT_MODEL_PARAMETERS.copy()
    state = json.loads(SEASON_STATE_PATH.read_text(encoding="utf-8"))
    if state.get("season") != season:
        return DEFAULT_MODEL_PARAMETERS.copy()
    parameters = state.get("model_parameters", DEFAULT_MODEL_PARAMETERS)
    if not isinstance(parameters, dict):
        raise ValueError(f"Invalid model parameters in {SEASON_STATE_PATH}")
    return {
        "intercept": float(parameters.get("intercept", 0.0)),
        "points_per_play_weight": float(
            parameters.get("points_per_play_weight", 0.5)
        ),
        "elo_weight": float(parameters.get("elo_weight", 0.5)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Project this week's FBS matchups from TeamRankings stats and Elo."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date.today(),
        help="Any date in the target week (YYYY-MM-DD; defaults to today)",
    )
    args = parser.parse_args()

    team_stats = fetch_team_stats(current_season_year(args.date))
    matchups = fetch_weekly_matchups(args.date, team_stats.keys())
    if not matchups:
        print(f"No FBS matchups found for the week containing {args.date.isoformat()}.")
        return

    elo_ratings = fetch_season_elo_ratings(
        current_season_year(args.date),
        args.date,
    )
    model_parameters = _saved_model_parameters(current_season_year(args.date))
    available_teams = {team_key(team) for team in team_stats}
    forecastable: list[Matchup] = []
    skipped: list[tuple[Matchup, list[str]]] = []
    for matchup in matchups:
        missing = [
            team
            for team in (matchup.home_team, matchup.away_team)
            if team_key(team) not in available_teams
        ]
        if missing:
            skipped.append((matchup, missing))
        else:
            forecastable.append(matchup)

    for matchup, missing in skipped:
        print(
            f"Skipping {matchup.away_team} at {matchup.home_team}: "
            f"TeamRankings stats unavailable for {', '.join(missing)}."
        )
    if not forecastable:
        print("No matchups have complete TeamRankings statistics.")
        return

    print(f"Weekly FBS projections for the week containing {args.date.isoformat()}")
    print(f"{'Matchup':48} {'Model score':19} {'Model margin':14} {'Market line':24} {'Edge':>8}")
    for matchup in forecastable:
        projection = project_matchup(
            matchup,
            team_stats,
            elo_ratings,
            model_parameters,
        )
        score = f"{projection.home_points:.1f}-{projection.away_points:.1f}"
        market_line = _market_line(matchup.market_home_margin, matchup.sportsbook)
        edge = (
            f"{projection.projected_home_margin - matchup.market_home_margin:+.1f}"
            if matchup.market_home_margin is not None
            else "n/a"
        )
        print(
            f"{matchup.away_team + ' at ' + matchup.home_team:48} "
            f"{score:19} {projection.projected_home_margin:+.1f} "
            f"{market_line:24} {edge:>8}"
        )


if __name__ == "__main__":
    main()
