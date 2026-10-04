from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gridiron_spread.data import Matchup, TeamStats, team_key
from gridiron_spread.model import (
    DEFAULT_MODEL_PARAMETERS,
    ELO_POINTS_PER_RATING,
    project_matchup,
)
from gridiron_spread.season_record import (
    empty_season_state,
    select_weekly_recommendations,
    season_record_summary,
)
from gridiron_spread.sources import (
    current_season_year,
    fetch_season_elo_ratings,
    fetch_team_stats,
    fetch_weekly_matchups,
)

SITE_DIR = ROOT / "website"
OUTPUT_DIR = ROOT / "dist"
SEASON_STATE_PATH = ROOT / "data" / "season_record.json"


def build_payload(
    reference_date: date,
    generated_at: datetime,
    matchups: list[Matchup],
    team_stats: dict[str, TeamStats],
    elo_ratings: dict[str, float] | None = None,
    season_state: dict[str, object] | None = None,
) -> dict[str, object]:
    stats_by_key = {team_key(name): stats for name, stats in team_stats.items()}
    season = current_season_year(reference_date)
    state = season_state or empty_season_state(season)
    state_parameters = state.get("model_parameters", DEFAULT_MODEL_PARAMETERS)
    if not isinstance(state_parameters, dict):
        state_parameters = DEFAULT_MODEL_PARAMETERS
    model_parameters = {
        "intercept": float(state_parameters.get("intercept", 0.0)),
        "points_per_play_weight": float(
            state_parameters.get("points_per_play_weight", 0.5)
        ),
        "elo_weight": float(state_parameters.get("elo_weight", 0.5)),
        "market_anchor_weight": float(
            state_parameters.get(
                "market_anchor_weight",
                DEFAULT_MODEL_PARAMETERS["market_anchor_weight"],
            )
        ),
    }
    games: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    for matchup in matchups:
        home_stats = stats_by_key.get(team_key(matchup.home_team))
        away_stats = stats_by_key.get(team_key(matchup.away_team))
        if home_stats is None or away_stats is None:
            missing = [
                name
                for name, stats in (
                    (matchup.home_team, home_stats),
                    (matchup.away_team, away_stats),
                )
                if stats is None
            ]
            skipped.append(
                {
                    "home_team": matchup.home_team,
                    "away_team": matchup.away_team,
                    "missing_stats": missing,
                }
            )
            market_lines = matchup.market_lines.copy()
            if (
                not market_lines
                and matchup.market_home_margin is not None
                and matchup.sportsbook is not None
            ):
                market_lines[matchup.sportsbook] = -matchup.market_home_margin
            provider_priority = ("FanDuel", "DraftKings", "ESPN", "SportsLine")
            sportsbook = matchup.sportsbook or next(
                (name for name in provider_priority if name in market_lines),
                None,
            )
            market_home_spread = (
                -matchup.market_home_margin
                if matchup.market_home_margin is not None
                else market_lines.get(sportsbook)
            )
            games.append(
                {
                    "event_id": matchup.event_id,
                    "home_team": matchup.home_team,
                    "away_team": matchup.away_team,
                    "start_time": matchup.start_time.isoformat(),
                    "predicted_home_spread": None,
                    "market_home_spread": (
                        round(market_home_spread, 1)
                        if market_home_spread is not None
                        else None
                    ),
                    "sportsbook": sportsbook,
                    "market_lines": {
                        name: round(spread, 1)
                        for name, spread in market_lines.items()
                    },
                    "prediction_unavailable": missing,
                    "recommendation": None,
                }
            )
            continue

        market_lines = matchup.market_lines.copy()
        if (
            not market_lines
            and matchup.market_home_margin is not None
            and matchup.sportsbook is not None
        ):
            market_lines[matchup.sportsbook] = -matchup.market_home_margin

        projection = project_matchup(
            matchup,
            team_stats,
            elo_ratings,
            model_parameters,
        )
        game = {
            "event_id": matchup.event_id,
            "home_team": matchup.home_team,
            "away_team": matchup.away_team,
            "start_time": matchup.start_time.isoformat(),
            "predicted_home_spread": round(-projection.projected_home_margin, 1),
            "independent_model_home_spread": round(
                -(
                    projection.model_intercept
                    + projection.points_per_play_weight
                    * (projection.home_points - projection.away_points)
                    + projection.elo_weight * projection.elo_home_margin
                ),
                1,
            ),
            "points_per_play_home_spread": round(
                -(projection.home_points - projection.away_points), 1
            ),
            "elo_home_spread": round(-projection.elo_home_margin, 1),
            "elo_rating_points_per_spread_point": ELO_POINTS_PER_RATING,
            "market_home_spread": (
                round(-matchup.market_home_margin, 1)
                if matchup.market_home_margin is not None
                else None
            ),
            "sportsbook": matchup.sportsbook,
            "market_lines": {
                name: round(spread, 1)
                for name, spread in market_lines.items()
            },
            "home_elo": round(
                (elo_ratings or {}).get(team_key(matchup.home_team), 1500.0), 1
            ),
            "away_elo": round(
                (elo_ratings or {}).get(team_key(matchup.away_team), 1500.0), 1
            ),
            "home_stats": {
                "offensive_points_per_play": home_stats.offensive_points_per_play,
                "offensive_plays_per_game": home_stats.offensive_plays_per_game,
                "opponent_points_per_play": home_stats.opponent_points_per_play,
                "opponent_plays_per_game": home_stats.opponent_plays_per_game,
            },
            "away_stats": {
                "offensive_points_per_play": away_stats.offensive_points_per_play,
                "offensive_plays_per_game": away_stats.offensive_plays_per_game,
                "opponent_points_per_play": away_stats.opponent_points_per_play,
                "opponent_plays_per_game": away_stats.opponent_plays_per_game,
            },
            "recommendation": None,
        }
        games.append(game)

    for game, bet in select_weekly_recommendations(games, generated_at):
        game["recommendation"] = bet

    week_start = reference_date - timedelta(days=reference_date.weekday())
    week_end = week_start + timedelta(days=6)
    return {
        "season": season,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "games": games,
        "skipped": skipped,
        "season_record": {
            **season_record_summary(state),
            "recommendations": state.get("recommendations", []),
            "updated_at": state.get("updated_at"),
            "model_calibration": state.get("model_calibration"),
        },
        "model_parameters": model_parameters,
        "sources": [
            {
                "name": "TeamRankings",
                "url": "https://www.teamrankings.com/college-football/",
            },
            {
                "name": "ESPN scoreboard",
                "url": "https://www.espn.com/college-football/scoreboard",
            },
        ],
    }


def build_site(reference_date: date | None = None) -> Path:
    reference_date = reference_date or date.today()
    generated_at = datetime.now(timezone.utc)
    stats = fetch_team_stats(current_season_year(reference_date))
    matchups = fetch_weekly_matchups(reference_date, stats.keys())
    season = current_season_year(reference_date)
    if SEASON_STATE_PATH.exists():
        season_state = json.loads(SEASON_STATE_PATH.read_text(encoding="utf-8"))
        if season_state.get("season") != season:
            season_state = empty_season_state(season)
    else:
        season_state = empty_season_state(season)
    saved_ratings = season_state.get("elo_ratings")
    elo_ratings = (
        saved_ratings
        if isinstance(saved_ratings, dict)
        else fetch_season_elo_ratings(season, reference_date)
    )
    payload = build_payload(
        reference_date,
        generated_at,
        matchups,
        stats,
        elo_ratings,
        season_state,
    )

    if not payload["games"] and not payload["skipped"]:
        raise RuntimeError(f"No FBS matchups found for the week of {reference_date.isoformat()}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    versioned_assets = {}
    for name in ("app.js", "styles.css"):
        content = (SITE_DIR / name).read_bytes()
        suffix = Path(name).suffix
        stem = Path(name).stem
        versioned_name = f"{stem}.{hashlib.sha256(content).hexdigest()[:12]}{suffix}"
        versioned_assets[name] = (versioned_name, content)

    for source in SITE_DIR.iterdir():
        if not source.is_file():
            continue
        if source.name in versioned_assets:
            versioned_name, content = versioned_assets[source.name]
            (OUTPUT_DIR / versioned_name).write_bytes(content)
        elif source.name == "index.html":
            html = source.read_text(encoding="utf-8")
            for name, (versioned_name, _) in versioned_assets.items():
                original_reference = f'"./{name}"'
                if original_reference not in html:
                    raise ValueError(f"Website index does not reference {name}")
                html = html.replace(original_reference, f'"./{versioned_name}"')
            (OUTPUT_DIR / source.name).write_text(html, encoding="utf-8")
        else:
            shutil.copy2(source, OUTPUT_DIR / source.name)
    (OUTPUT_DIR / "data.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return OUTPUT_DIR


if __name__ == "__main__":
    output = build_site()
    print(f"Built the weekly dashboard in {output}")
