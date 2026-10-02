from datetime import date, datetime, timezone
import json

import pytest

from gridiron_spread.data import EloGame, Matchup, TeamStats, team_key
from gridiron_spread.model import predict_team_points, project_matchup
from gridiron_spread.season_record import (
    recommended_bet,
    season_record_summary,
    update_season_state,
)
from gridiron_spread import sources
from gridiron_spread.sources import (
    _espn_odds,
    _espn_market_lines,
    _read_team_stat,
    calculate_elo_ratings,
    completed_elo_games,
    fetch_sportsline_spreads,
)
from scripts.build_site import build_payload
from scripts.update_season import _select_mode


def test_predict_team_points_averages_offense_and_opponent_output():
    team = TeamStats("Home", 0.5, 70, 65, 0.3)
    opponent = TeamStats("Away", 0.4, 68, 72, 0.35)

    points = predict_team_points(team, opponent)

    assert points == pytest.approx((0.5 * 70 + 0.35 * 72) / 2)


def test_project_matchup_blends_points_per_play_and_elo_margins():
    home = TeamStats("Home", 0.5, 70, 65, 0.3)
    away = TeamStats("Away", 0.4, 68, 72, 0.35)
    matchup = Matchup(
        home_team="Home",
        away_team="Away",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=3.5,
        sportsbook="FanDuel",
    )

    projection = project_matchup(
        matchup,
        {"Home": home, "Away": away},
        {"home": 1600, "away": 1500},
    )

    assert projection.home_points == pytest.approx((35 + 25.2) / 2)
    assert projection.away_points == pytest.approx((27.2 + 19.5) / 2)
    assert projection.elo_home_margin == pytest.approx(4)
    assert projection.projected_home_margin == pytest.approx(5.375)


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


def test_odds_use_draftkings_when_fanduel_line_is_invalid():
    competition = {
        "odds": [
            {
                "provider": {"name": "FanDuel"},
                "spread": "--",
                "homeTeamOdds": {"favorite": True},
            },
            {
                "provider": {"name": "DraftKings"},
                "spread": 4.5,
                "homeTeamOdds": {"favorite": False},
            },
        ]
    }

    assert _espn_odds(competition) == (-4.5, "DraftKings")


def _sportsline_html() -> str:
    state = {
        "ROOT_QUERY": {
            'odds({"league":"ncaaf"})': {
                "oddsCompetitions": [{"__ref": "OddsCompetitionDTO:1"}]
            }
        },
        "OddsCompetitionDTO:1": {
            "homeTeamId": 1,
            "awayTeamId": 2,
            "scheduledTime": "2026-10-03T16:00:00Z",
            "sportsBookOdds": {
                "fanduel": {"spread": {"home": {"value": "+3.5"}}},
                "draftkings": {"spread": {"home": {"value": "+4"}}},
                "consensus": {"spread": {"home": {"value": "+4.5"}}},
            },
        },
        "CompetitionDTOTeam:1": {"location": "Home"},
        "CompetitionDTOTeam:2": {"location": "Away"},
    }
    flight_data = f'"apolloState":{json.dumps(state)}'
    return f"<script>self.__next_f.push([1,{json.dumps(flight_data)}])</script>"


def test_sportsline_parser_reads_consensus_home_spread():
    assert fetch_sportsline_spreads(_sportsline_html()) == {
        ("home", "away", date(2026, 10, 3)): {
            "FanDuel": 3.5,
            "DraftKings": 4.0,
            "SportsLine": 4.5,
        }
    }


def test_espn_odds_parser_keeps_each_provider_and_feed_line():
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

    assert _espn_market_lines(competition) == {
        "ESPN": 4.5,
        "DraftKings": 4.5,
        "FanDuel": -3.5,
    }


def test_weekly_matchups_collect_lines_from_each_available_source(monkeypatch):
    def event(event_id, home, away, odds):
        return {
            "id": event_id,
            "date": "2026-10-03T16:00:00Z",
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"location": home}},
                        {"homeAway": "away", "team": {"location": away}},
                    ],
                    "odds": odds,
                }
            ],
        }

    events = [
        event("1", "Home", "Away", []),
        event(
            "2",
            "Other Home",
            "Other Away",
            [
                {
                    "provider": {"name": "FanDuel"},
                    "spread": 4.5,
                    "homeTeamOdds": {"favorite": True},
                }
            ],
        ),
        event("3", "No Line Home", "No Line Away", []),
    ]

    def fetch(url):
        if url.startswith(sources.ESPN_SCOREBOARD_URL):
            return json.dumps({"events": events if "dates=20261003" in url else []})
        if url == sources.SPORTSLINE_ODDS_URL:
            return _sportsline_html()
        raise AssertionError(f"Unexpected source URL: {url}")

    monkeypatch.setattr(sources, "_fetch_text", fetch)
    matchups = sources.fetch_weekly_matchups(date(2026, 10, 3))

    assert matchups[0].market_home_margin == -3.5
    assert matchups[0].sportsbook == "FanDuel"
    assert matchups[0].market_lines == {
        "FanDuel": 3.5,
        "DraftKings": 4.0,
        "SportsLine": 4.5,
    }
    assert matchups[1].market_home_margin == 4.5
    assert matchups[1].sportsbook == "FanDuel"
    assert matchups[1].market_lines == {
        "FanDuel": -4.5,
        "ESPN": -4.5,
    }
    assert matchups[2].market_home_margin is None
    assert matchups[2].sportsbook is None


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
        market_lines={"FanDuel": -3.5, "DraftKings": -4.0},
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
    assert payload["games"][0]["predicted_home_spread"] == -3.4
    assert payload["games"][0]["market_home_spread"] == -3.5
    assert "home_points" not in payload["games"][0]
    assert "away_points" not in payload["games"][0]
    assert "projected_home_margin" not in payload["games"][0]
    assert payload["games"][0]["sportsbook"] == "FanDuel"
    assert payload["games"][0]["market_lines"] == {
        "FanDuel": -3.5,
        "DraftKings": -4.0,
    }
    assert payload["games"][0]["home_elo"] == 1500
    assert payload["season_record"]["wins"] == 0
    assert payload["skipped"] == [
        {"home_team": "UAB", "away_team": "Samford", "missing_stats": ["Samford"]}
    ]


def test_calculate_elo_ratings_applies_expected_score_update():
    games = [
        EloGame(
            event_id="1",
            home_team="Home",
            away_team="Away",
            home_score=28,
            away_score=21,
            start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
    ]

    ratings = calculate_elo_ratings(games)

    assert ratings["home"] == pytest.approx(1510)
    assert ratings["away"] == pytest.approx(1490)


def test_completed_elo_games_only_accepts_final_espn_events():
    event = {
        "id": "42",
        "date": "2026-09-01T20:00:00Z",
        "competitions": [
            {
                "status": {"type": {"state": "post"}},
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"location": "Home"},
                        "score": "21",
                    },
                    {
                        "homeAway": "away",
                        "team": {"location": "Away"},
                        "score": "28",
                    },
                ],
            }
        ],
    }
    scheduled = json.loads(json.dumps(event))
    scheduled["id"] = "43"
    scheduled["competitions"][0]["status"]["type"]["state"] = "in"

    results = completed_elo_games([event, scheduled])

    assert len(results) == 1
    assert results[0].event_id == "42"
    assert results[0].away_score == 28


@pytest.mark.parametrize(
    ("instant", "expected_mode"),
    [
        ("2026-10-07T13:00:00+00:00", "refresh"),
        ("2026-10-07T14:00:00+00:00", None),
        ("2027-01-06T14:00:00+00:00", "refresh"),
        ("2026-10-05T04:00:00+00:00", "grade"),
        ("2027-01-04T05:00:00+00:00", "grade"),
    ],
)
def test_scheduled_mode_matches_central_time_with_daylight_saving(
    instant, expected_mode
):
    assert _select_mode("scheduled", datetime.fromisoformat(instant)) == expected_mode


def test_recommended_bet_picks_model_favored_side_at_three_points():
    game = {
        "event_id": "123",
        "home_team": "Home",
        "away_team": "Away",
        "predicted_home_spread": -7,
        "market_home_spread": -3.0,
        "sportsbook": "FanDuel",
    }

    assert recommended_bet(game) == {
        "side": "home",
        "team": "Home",
        "spread": -3.0,
        "difference": 4.0,
        "sportsbook": "FanDuel",
    }
    game["predicted_home_spread"] = -6
    assert recommended_bet(game)["difference"] == 3
    game["predicted_home_spread"] = -5.9
    assert recommended_bet(game) is None


@pytest.mark.parametrize(
    ("home_score", "away_score", "expected_status"),
    [(24, 20, "win"), (20, 24, "loss"), (23, 20, "push")],
)
def test_season_record_grades_spread_picks_and_tracks_record(
    home_score, away_score, expected_status
):
    kickoff = datetime(2026, 9, 5, 18, tzinfo=timezone.utc)
    now = datetime(2026, 9, 6, 4, tzinfo=timezone.utc)
    game = {
        "event_id": "42",
        "home_team": "Home",
        "away_team": "Away",
        "start_time": kickoff.isoformat(),
        "predicted_home_spread": -7,
        "market_home_spread": -3.0,
        "sportsbook": "FanDuel",
    }
    pick_state = update_season_state(
        None,
        2026,
        {},
        [game],
        [],
        datetime(2026, 9, 4, tzinfo=timezone.utc),
        "refresh",
    )
    completed = EloGame(
        event_id="42",
        home_team="Home",
        away_team="Away",
        home_score=home_score,
        away_score=away_score,
        start_time=kickoff,
    )

    graded = update_season_state(
        pick_state,
        2026,
        {},
        [],
        [completed],
        now,
        "grade",
    )

    assert graded["recommendations"][0]["status"] == expected_status
    summary = season_record_summary(graded)
    assert summary[{"win": "wins", "loss": "losses", "push": "pushes"}[expected_status]] == 1


def test_refresh_replaces_pending_pick_with_latest_qualifying_line():
    kickoff = datetime(2026, 10, 3, 18, tzinfo=timezone.utc)
    game = {
        "event_id": "42",
        "home_team": "Home",
        "away_team": "Away",
        "start_time": kickoff.isoformat(),
        "predicted_home_spread": -8,
        "market_home_spread": -4,
        "sportsbook": "FanDuel",
    }
    first = update_season_state(
        None,
        2026,
        {},
        [game],
        [],
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        "refresh",
    )
    game["market_home_spread"] = -3.5
    game["sportsbook"] = "DraftKings"
    latest = update_season_state(
        first,
        2026,
        {},
        [game],
        [],
        datetime(2026, 10, 2, tzinfo=timezone.utc),
        "refresh",
    )

    assert len(latest["recommendations"]) == 1
    assert latest["recommendations"][0]["spread"] == -3.5
    assert latest["recommendations"][0]["sportsbook"] == "DraftKings"
    assert latest["recommendations"][0]["recommended_at"] == "2026-10-02T00:00:00+00:00"
