from datetime import date, datetime, timezone

import pytest

from gridiron_spread.data import Matchup, TeamStats, team_key
from gridiron_spread.model import predict_team_points, project_matchup
from gridiron_spread.sources import _espn_odds, _read_team_stat
from scripts.build_site import build_payload


def test_predict_team_points_averages_offense_and_opponent_output():
    team = TeamStats("Home", 0.5, 70, 65, 0.3)
    opponent = TeamStats("Away", 0.4, 68, 72, 0.35)

    points = predict_team_points(team, opponent)

    assert points == pytest.approx((0.5 * 70 + 0.35 * 72) / 2)


def test_project_matchup_calculates_each_team_points_and_margin():
    home = TeamStats("Home", 0.5, 70, 65, 0.3)
    away = TeamStats("Away", 0.4, 68, 72, 0.35)
    matchup = Matchup(
        home_team="Home",
        away_team="Away",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=3.5,
        sportsbook="FanDuel",
    )

    projection = project_matchup(matchup, {"Home": home, "Away": away})

    assert projection.home_points == pytest.approx((35 + 25.2) / 2)
    assert projection.away_points == pytest.approx((27.2 + 19.5) / 2)
    assert projection.projected_home_margin == pytest.approx(6.75)


def test_project_matchup_matches_abbreviated_team_names():
    matchup = Matchup(
        home_team="Miami FL",
        away_team="Miami OH",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=None,
        sportsbook=None,
    )
    stats = {
        "Miami (FL)": TeamStats("Miami (FL)", 0.5, 70, 65, 0.3),
        "Miami (OH)": TeamStats("Miami (OH)", 0.4, 68, 72, 0.35),
    }

    projection = project_matchup(matchup, stats)

    assert projection.home_points == pytest.approx((35 + 25.2) / 2)


def test_project_matchup_reports_missing_team_stats():
    matchup = Matchup(
        home_team="Home",
        away_team="Missing",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=None,
        sportsbook=None,
    )

    with pytest.raises(ValueError, match="Missing TeamRankings statistics for Missing"):
        project_matchup(matchup, {"Home": TeamStats("Home", 0.5, 70, 65, 0.3)})


def test_teamrankings_parser_reads_the_requested_season_column():
    html = """
    <table class="tr-table datatable scrollable">
      <thead><tr><th>Rank</th><th>Team</th><th>2026</th><th>Last 3</th></tr></thead>
      <tbody><tr>
        <td data-sort="1">1</td>
        <td data-sort="Georgia"><a>Georgia</a></td>
        <td data-sort="0.45">0.450</td>
        <td data-sort="0.5">0.500</td>
      </tr></tbody>
    </table>
    """

    assert _read_team_stat(html, 2026, "test-url") == {"georgia": ("Georgia", 0.45)}


def test_odds_prefer_fanduel_then_use_labeled_draftkings_fallback():
    competition = {
        "odds": [
            {
                "provider": {"name": "DraftKings"},
                "spread": 4.5,
                "homeTeamOdds": {"favorite": False},
            },
            {
                "provider": {"name": "FanDuel"},
                "spread": 3.5,
                "homeTeamOdds": {"favorite": True},
            },
        ]
    }

    assert _espn_odds(competition) == (3.5, "FanDuel")
    competition["odds"].pop()
    assert _espn_odds(competition) == (-4.5, "DraftKings")


@pytest.mark.parametrize(
    ("schedule_name", "stats_name"),
    [
        ("Western Kentucky", "W Kentucky"),
        ("Central Michigan", "C Michigan"),
        ("Massachusetts", "UMass"),
        ("Middle Tennessee", "Middle Tenn"),
    ],
)
def test_team_name_aliases_match_teamrankings(schedule_name, stats_name):
    assert team_key(schedule_name) == team_key(stats_name)


def test_website_payload_serializes_projection_and_skipped_games():
    matchup = Matchup(
        home_team="Home",
        away_team="Away",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=3.5,
        sportsbook="FanDuel",
    )
    missing = Matchup(
        home_team="UAB",
        away_team="Samford",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=None,
        sportsbook=None,
    )
    stats = {
        "Home": TeamStats("Home", 0.5, 70, 65, 0.3),
        "Away": TeamStats("Away", 0.4, 68, 72, 0.35),
        "UAB": TeamStats("UAB", 0.4, 70, 65, 0.32),
    }

    payload = build_payload(
        date(2026, 10, 3),
        datetime(2026, 10, 2, tzinfo=timezone.utc),
        [matchup, missing],
        stats,
    )

    assert payload["week_start"] == "2026-09-28"
    assert payload["week_end"] == "2026-10-04"
    assert len(payload["games"]) == 1
    assert payload["games"][0]["projected_home_margin"] == 6.8
    assert payload["games"][0]["sportsbook"] == "FanDuel"
    assert payload["skipped"] == [
        {"home_team": "UAB", "away_team": "Samford", "missing_stats": ["Samford"]}
    ]
