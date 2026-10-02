from __future__ import annotations

from .data import Matchup, Projection, TeamStats, team_key

ELO_POINTS_PER_RATING = 25


def predict_team_points(team_stats: TeamStats, opponent_stats: TeamStats) -> float:
    return (team_stats.offensive_points_per_game + opponent_stats.opponent_points_per_game) / 2


def project_matchup(
    matchup: Matchup,
    team_stats: dict[str, TeamStats],
    elo_ratings: dict[str, float] | None = None,
) -> Projection:
    stats_by_key = {team_key(name): stats for name, stats in team_stats.items()}
    home_stats = stats_by_key.get(team_key(matchup.home_team))
    away_stats = stats_by_key.get(team_key(matchup.away_team))
    missing = [
        team
        for team, stats in (
            (matchup.home_team, home_stats),
            (matchup.away_team, away_stats),
        )
        if stats is None
    ]
    if missing:
        raise ValueError(f"Missing TeamRankings statistics for {', '.join(missing)}")

    ratings_by_key = {
        team_key(name): rating for name, rating in (elo_ratings or {}).items()
    }
    home_rating = ratings_by_key.get(team_key(matchup.home_team), 1500.0)
    away_rating = ratings_by_key.get(team_key(matchup.away_team), 1500.0)

    return Projection(
        matchup=matchup,
        home_points=predict_team_points(home_stats, away_stats),
        away_points=predict_team_points(away_stats, home_stats),
        elo_home_margin=(home_rating - away_rating) / ELO_POINTS_PER_RATING,
    )
