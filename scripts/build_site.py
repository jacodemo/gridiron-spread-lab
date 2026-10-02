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
from gridiron_spread.model import project_matchup
from gridiron_spread.sources import current_season_year, fetch_team_stats, fetch_weekly_matchups

SITE_DIR = ROOT / "website"
OUTPUT_DIR = ROOT / "dist"


def build_payload(
    reference_date: date,
    generated_at: datetime,
    matchups: list[Matchup],
    team_stats: dict[str, TeamStats],
) -> dict[str, object]:
    stats_by_key = {team_key(name): stats for name, stats in team_stats.items()}
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
            continue

        projection = project_matchup(matchup, team_stats)
        games.append(
            {
                "home_team": matchup.home_team,
                "away_team": matchup.away_team,
                "start_time": matchup.start_time.isoformat(),
                "predicted_home_spread": round(-projection.projected_home_margin, 1),
                "market_home_spread": (
                    round(-matchup.market_home_margin, 1)
                    if matchup.market_home_margin is not None
                    else None
                ),
                "sportsbook": matchup.sportsbook,
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
            }
        )

    week_start = reference_date - timedelta(days=reference_date.weekday())
    week_end = week_start + timedelta(days=6)
    return {
        "season": current_season_year(reference_date),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "games": games,
        "skipped": skipped,
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
    matchups = fetch_weekly_matchups(reference_date)
    payload = build_payload(reference_date, generated_at, matchups, stats)

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
