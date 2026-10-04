from datetime import date, datetime, timezone
import json

import pytest

from gridiron_spread.data import EloGame, Matchup, TeamStats, team_key
from gridiron_spread import cli
from gridiron_spread.model import (
    DEFAULT_MODEL_PARAMETERS,
    calibrate_model_parameters,
    predict_team_points,
    project_matchup,
)
from gridiron_spread.season_record import (
    recommended_bet,
    select_weekly_recommendations,
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
    fetch_vegasinsider_spreads,
    _canonical_team_name,
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
    assert projection.projected_home_margin == pytest.approx(4.0625)


def test_project_matchup_uses_independent_projection_without_market_line():
    home = TeamStats("Home", 0.5, 70, 65, 0.3)
    away = TeamStats("Away", 0.4, 68, 72, 0.35)
    matchup = Matchup(
        home_team="Home",
        away_team="Away",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=None,
        sportsbook=None,
    )

    projection = project_matchup(
        matchup,
        {"Home": home, "Away": away},
        {"Home": 1600, "Away": 1500},
    )

    assert projection.projected_home_margin == pytest.approx(5.375)


def test_project_matchup_uses_calibrated_model_parameters():
    home = TeamStats("Home", 0.5, 70, 65, 0.3)
    away = TeamStats("Away", 0.4, 68, 72, 0.35)
    matchup = Matchup(
        home_team="Home",
        away_team="Away",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=None,
        sportsbook=None,
    )

    projection = project_matchup(
        matchup,
        {"Home": home, "Away": away},
        {"Home": 1600, "Away": 1500},
        {
            "intercept": 2.0,
            "points_per_play_weight": 0.8,
            "elo_weight": 0.2,
        },
    )

    assert projection.projected_home_margin == pytest.approx(8.2)


def test_model_calibration_requires_walk_forward_improvement():
    records = []
    for index in range(100):
        points_margin = ((index * 17) % 43) - 21
        elo_margin = ((index * 11) % 19) - 9
        records.append(
            {
                "start_time": datetime(
                    2026, 9, 1 + index // 10, tzinfo=timezone.utc
                ).isoformat(),
                "points_per_play_home_margin": points_margin,
                "elo_home_margin": elo_margin,
                "actual_home_margin": 3 + 1.1 * points_margin + 0.05 * elo_margin,
            }
        )

    parameters, report = calibrate_model_parameters(records)

    assert report["status"] == "updated"
    assert report["observations"] == 100
    assert report["calibrated_validation_mae"] < report["baseline_validation_mae"]
    assert parameters["points_per_play_weight"] > DEFAULT_MODEL_PARAMETERS[
        "points_per_play_weight"
    ]


def test_market_anchored_calibration_validates_the_blended_forecast():
    records = []
    market_weight = DEFAULT_MODEL_PARAMETERS["market_anchor_weight"]
    for index in range(100):
        points_margin = ((index * 17) % 43) - 21
        elo_margin = ((index * 11) % 19) - 9
        market_margin = ((index * 13) % 47) - 23
        independent_margin = 3 + 1.1 * points_margin + 0.05 * elo_margin
        records.append(
            {
                "start_time": datetime(
                    2026, 9, 1 + index // 10, tzinfo=timezone.utc
                ).isoformat(),
                "points_per_play_home_margin": points_margin,
                "elo_home_margin": elo_margin,
                "market_home_margin": market_margin,
                "actual_home_margin": (
                    market_weight * market_margin
                    + (1 - market_weight) * independent_margin
                ),
            }
        )

    parameters, report = calibrate_model_parameters(records)

    assert report["status"] == "updated"
    assert report["calibrated_validation_mae"] < report["baseline_validation_mae"]
    assert parameters["market_anchor_weight"] == market_weight
    assert parameters["points_per_play_weight"] > 0.5


def test_model_calibration_warms_up_without_changing_parameters():
    records = [
        {
            "start_time": datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat(),
            "points_per_play_home_margin": 4.0,
            "elo_home_margin": 2.0,
            "actual_home_margin": 8.0,
        }
        for _ in range(39)
    ]

    parameters, report = calibrate_model_parameters(records)

    assert report["status"] == "warming_up"
    assert parameters == DEFAULT_MODEL_PARAMETERS


def test_cli_uses_saved_parameters_for_the_current_season(tmp_path, monkeypatch):
    state_path = tmp_path / "season_record.json"
    state_path.write_text(
        json.dumps(
            {
                "season": 2026,
                "model_parameters": {
                    "intercept": 2,
                    "points_per_play_weight": 0.8,
                    "elo_weight": 0.2,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "SEASON_STATE_PATH", state_path)

    assert cli._saved_model_parameters(2026) == {
        "intercept": 2.0,
        "points_per_play_weight": 0.8,
        "elo_weight": 0.2,
        "market_anchor_weight": DEFAULT_MODEL_PARAMETERS["market_anchor_weight"],
    }
    assert cli._saved_model_parameters(2025) == DEFAULT_MODEL_PARAMETERS


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


def _vegasinsider_html() -> str:
    return """
    <table>
      <tr>
        <td class="game-time"><span data-value="2026-10-03T23:00:00Z"></span></td>
        <th class="book-logo">Open</th>
        <th class="book-logo">Consensus</th>
      </tr>
      <tr class="divided">
        <td class="game-team"><a class="team-name" data-abbr="BYU">BYU</a></td>
        <td class="game-odds"><span class="data-value">-5.5</span><small>-110</small></td>
        <td class="game-odds"><span class="data-value">-6</span><small>-110</small></td>
        <td class="game-odds blank"></td>
      </tr>
      <tr class="footer">
        <td class="game-team"><a class="team-name" data-abbr="TCU">TCU</a></td>
        <td class="game-odds"><span class="data-value">+5.5</span><small>-110</small></td>
        <td class="game-odds"><span class="data-value">+6</span><small>-110</small></td>
        <td class="game-odds blank"></td>
      </tr>
    </table>
    """


def test_sportsline_parser_reads_consensus_home_spread():
    assert fetch_sportsline_spreads(_sportsline_html()) == {
        ("home", "away", date(2026, 10, 3)): {
            "FanDuel": 3.5,
            "DraftKings": 4.0,
            "SportsLine": 4.5,
        }
    }


def test_vegasinsider_parser_reads_home_consensus_spread():
    assert fetch_vegasinsider_spreads(_vegasinsider_html()) == {
        ("tcu", "byu", date(2026, 10, 3)): 6.0,
    }


def test_vegasinsider_parser_keeps_final_game_lines_without_date():
    html = """
    <table>
      <tr>
        <td class="game-time">Final</td>
        <th class="book-logo">Open</th>
        <th class="book-logo">Consensus</th>
      </tr>
      <tr class="divided">
        <td class="game-team"><a class="team-name" data-abbr="VAN" aria-label="Vanderbilt">Vanderbilt</a></td>
        <td class="game-odds"><span class="data-value">+25.5</span></td>
        <td class="game-odds"><span class="data-value">+25.5</span></td>
        <td class="game-odds blank"></td>
      </tr>
      <tr class="footer">
        <td class="game-team"><a class="team-name" data-abbr="UGA" aria-label="Georgia">Georgia</a></td>
        <td class="game-odds"><span class="data-value">-25.5</span></td>
        <td class="game-odds"><span class="data-value">-25.5</span></td>
        <td class="game-odds blank"></td>
      </tr>
    </table>
    """

    assert fetch_vegasinsider_spreads(html) == {
        ("georgia", "vanderbilt", None): -25.5,
    }


def test_weekly_matchups_uses_vegasinsider_lines_for_final_games(monkeypatch):
    event = {
        "id": "401858999",
        "date": "2026-10-03T16:45:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"location": "Georgia"}},
                    {"homeAway": "away", "team": {"location": "Vanderbilt"}},
                ],
                "odds": [],
            }
        ],
    }

    def fetch(url):
        if url.startswith(sources.ESPN_SCOREBOARD_URL):
            return json.dumps({"events": [event]})
        if url == sources.SPORTSLINE_ODDS_URL:
            return _sportsline_html()
        if url == sources.VEGASINSIDER_ODDS_URL:
            return """
            <table>
              <tr>
                <td class="game-time">Final</td>
                <th class="book-logo">Open</th>
                <th class="book-logo">Consensus</th>
              </tr>
              <tr class="divided">
                <td class="game-team"><a class="team-name" data-abbr="VAN" aria-label="Vanderbilt">Vanderbilt</a></td>
                <td class="game-odds"><span class="data-value">+25.5</span></td>
                <td class="game-odds"><span class="data-value">+25.5</span></td>
                <td class="game-odds blank"></td>
              </tr>
              <tr class="footer">
                <td class="game-team"><a class="team-name" data-abbr="UGA" aria-label="Georgia">Georgia</a></td>
                <td class="game-odds"><span class="data-value">-25.5</span></td>
                <td class="game-odds"><span class="data-value">-25.5</span></td>
                <td class="game-odds blank"></td>
              </tr>
            </table>
            """
        raise AssertionError(f"Unexpected source URL: {url}")

    monkeypatch.setattr(sources, "_fetch_text", fetch)
    matchups = sources.fetch_weekly_matchups(date(2026, 10, 3))

    assert len(matchups) == 1
    assert matchups[0].market_lines == {"VegasInsider": -25.5}
    assert matchups[0].market_home_margin == 25.5
    assert matchups[0].sportsbook == "VegasInsider"


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
        if url == sources.VEGASINSIDER_ODDS_URL:
            return "<html></html>"
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


def test_weekly_matchups_uses_vegasinsider_when_no_other_line_exists(monkeypatch):
    event = {
        "id": "401856818",
        "date": "2026-10-03T23:00:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"location": "TCU"}},
                    {"homeAway": "away", "team": {"location": "BYU"}},
                ],
                "odds": [],
            }
        ],
    }

    def fetch(url):
        if url.startswith(sources.ESPN_SCOREBOARD_URL):
            return json.dumps({"events": [event]})
        if url == sources.SPORTSLINE_ODDS_URL:
            return _sportsline_html()
        if url == sources.VEGASINSIDER_ODDS_URL:
            return _vegasinsider_html()
        raise AssertionError(f"Unexpected source URL: {url}")

    monkeypatch.setattr(sources, "_fetch_text", fetch)
    matchups = sources.fetch_weekly_matchups(date(2026, 10, 3))

    assert len(matchups) == 1
    assert matchups[0].market_lines == {"VegasInsider": 6.0}
    assert matchups[0].market_home_margin == -6.0
    assert matchups[0].sportsbook == "VegasInsider"


def test_weekly_matchups_supplement_schedule_games(monkeypatch):
    schedule_event = {
        "id": "401856818",
        "date": "2026-10-03T23:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"location": "TCU"}},
                    {"homeAway": "away", "team": {"location": "BYU"}},
                ],
                "odds": [],
            }
        ],
    }
    teams_payload = {
        "sports": [
            {
                "leagues": [
                    {
                        "teams": [
                            {"team": {"id": "2628", "location": "TCU", "isActive": True}},
                            {"team": {"id": "252", "location": "BYU", "isActive": True}},
                        ]
                    }
                ]
            }
        ]
    }
    requested_schedules: list[str] = []

    def fetch(url):
        if url.startswith(sources.ESPN_SCOREBOARD_URL):
            return json.dumps({"events": []})
        if url == f"{sources.ESPN_TEAMS_URL}?limit=1000":
            return json.dumps(teams_payload)
        if url.startswith(f"{sources.ESPN_TEAMS_URL}/"):
            requested_schedules.append(url)
            return json.dumps({"events": [schedule_event]})
        if url == sources.SPORTSLINE_ODDS_URL:
            return _sportsline_html()
        if url == sources.VEGASINSIDER_ODDS_URL:
            return _vegasinsider_html()
        raise AssertionError(f"Unexpected source URL: {url}")

    monkeypatch.setattr(sources, "_fetch_text", fetch)
    matchups = sources.fetch_weekly_matchups(
        date(2026, 10, 3),
        team_names=("TCU", "BYU"),
    )

    assert len(matchups) == 1
    assert matchups[0].event_id == "401856818"
    assert matchups[0].home_team == "TCU"
    assert matchups[0].away_team == "BYU"
    assert matchups[0].market_home_margin == -6.0
    assert matchups[0].sportsbook == "VegasInsider"
    assert matchups[0].market_lines == {"VegasInsider": 6.0}
    assert len(requested_schedules) == 2


@pytest.mark.parametrize(
    ("schedule_name", "stats_name"),
    [
        ("Ark", "Arkansas"),
        ("Boise", "Boise State"),
        ("CC", "Coastal Carolina"),
        ("Clem", "Clemson"),
        ("Colo", "Colorado"),
        ("CSU", "Colorado State"),
        ("FAU", "Florida Atlantic"),
        ("GASO", "Georgia Southern"),
        ("LT", "Louisiana Tech"),
        ("MCN", "McNeese"),
        ("MIA", "Miami (FL)"),
        ("ORST", "Oregon State"),
        ("TA&M", "Texas A&M"),
        ("TEM", "Temple"),
        ("TTU", "Texas Tech"),
        ("TXSO", "Texas Southern"),
        ("UL Monroe", "Louisiana Monroe"),
        ("ULM", "Louisiana Monroe"),
        ("USA", "South Alabama"),
        ("USF", "South Florida"),
        ("USU", "Utah State"),
        ("WASH", "Washington"),
        ("Western Kentucky", "W Kentucky"),
        ("Central Michigan", "C Michigan"),
        ("Massachusetts", "UMass"),
        ("Middle Tennessee", "Middle Tenn"),
    ],
)
def test_team_name_aliases_match_teamrankings(schedule_name, stats_name):
    assert team_key(schedule_name) == team_key(stats_name)


def test_miami_program_names_are_canonical_and_distinct():
    miami_fl = _canonical_team_name({"abbreviation": "MIA", "location": "Miami"})
    miami_oh = _canonical_team_name({"abbreviation": "M-OH", "location": "Miami"})

    assert miami_fl == "Miami (FL)"
    assert miami_oh == "Miami (OH)"
    assert team_key("Miami") == team_key(miami_fl)
    assert team_key(miami_fl) != team_key(miami_oh)


def test_project_matchup_matches_teamrankings_miami_to_florida_program():
    matchup = Matchup(
        home_team="Clemson",
        away_team="Miami (FL)",
        start_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        market_home_margin=None,
        sportsbook=None,
    )
    stats = {
        "Clemson": TeamStats("Clemson", 0.4, 70, 65, 0.3),
        "Miami": TeamStats("Miami", 0.5, 70, 65, 0.3),
        "Miami (OH)": TeamStats("Miami (OH)", 0.2, 68, 72, 0.35),
    }

    projection = project_matchup(matchup, stats)

    assert projection.away_points == pytest.approx((35 + 19.5) / 2)


def test_team_key_normalizes_diacritics():
    assert team_key("San José State") == team_key("San Jose St")


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
    assert len(payload["games"]) == 2
    assert payload["games"][0]["predicted_home_spread"] == -3.5
    assert payload["games"][0]["independent_model_home_spread"] == -3.4
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
    assert payload["games"][1]["predicted_home_spread"] is None
    assert payload["games"][1]["market_home_spread"] is None
    assert payload["games"][1]["prediction_unavailable"] == ["Samford"]
    assert payload["season_record"]["wins"] == 0
    assert payload["skipped"] == [
        {"home_team": "UAB", "away_team": "Samford", "missing_stats": ["Samford"]}
    ]


def test_website_payload_uses_the_locked_recommendation_from_season_state():
    kickoff = datetime(2026, 10, 3, 18, tzinfo=timezone.utc)
    matchup = Matchup(
        home_team="Home",
        away_team="Away",
        start_time=kickoff,
        market_home_margin=3.5,
        sportsbook="DraftKings",
        event_id="locked-game",
        market_lines={"DraftKings": -3.5},
    )
    stats = {
        "Home": TeamStats("Home", 0.5, 70, 65, 0.3),
        "Away": TeamStats("Away", 0.4, 68, 72, 0.35),
    }
    saved_pick = {
        "event_id": "locked-game",
        "home_team": "Home",
        "away_team": "Away",
        "start_time": kickoff.isoformat(),
        "side": "home",
        "team": "Home",
        "spread": -4.0,
        "difference": 5.25,
        "sportsbook": "FanDuel",
        "status": "pending",
    }

    payload = build_payload(
        date(2026, 10, 3),
        datetime(2026, 10, 2, tzinfo=timezone.utc),
        [matchup],
        stats,
        season_state={"season": 2026, "recommendations": [saved_pick]},
    )

    assert payload["games"][0]["recommendation"] == {
        "side": "home",
        "team": "Home",
        "spread": -4.0,
        "difference": 5.25,
        "sportsbook": "FanDuel",
    }


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
    game["predicted_home_spread"] = None
    assert recommended_bet(game) is None


def test_weekly_recommendations_select_at_least_ten_and_at_most_fifteen():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    games = [
        {
            "event_id": str(index),
            "home_team": f"Home {index}",
            "away_team": f"Away {index}",
            "start_time": datetime(2026, 10, 3, tzinfo=timezone.utc).isoformat(),
            "market_home_spread": 0.0,
            "predicted_home_spread": -(0.5 + index * 0.2),
            "sportsbook": "FanDuel",
        }
        for index in range(20)
    ]

    picks = select_weekly_recommendations(games, now)

    assert len(picks) == 10
    assert [game["event_id"] for game, _ in picks] == [
        str(index) for index in range(19, 9, -1)
    ]
    assert any(abs(bet["difference"]) < 3 for _, bet in picks)

    larger_slate = games + [
        {
            **game,
            "event_id": f"extra-{game['event_id']}",
            "predicted_home_spread": game["predicted_home_spread"] - 3.0,
        }
        for game in games
    ]
    capped_picks = select_weekly_recommendations(larger_slate, now)

    assert len(capped_picks) == 15
    assert all(abs(bet["difference"]) >= 3 for _, bet in capped_picks[10:])


def test_refresh_locks_recommendations_and_saved_lines_for_the_week():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    kickoff = datetime(2026, 10, 3, tzinfo=timezone.utc).isoformat()
    games = [
        {
            "event_id": str(index),
            "home_team": f"Home {index}",
            "away_team": f"Away {index}",
            "start_time": kickoff,
            "predicted_home_spread": -(0.5 + float(index) * 0.1),
            "points_per_play_home_spread": -float(index + 1),
            "elo_home_spread": 0.0,
            "market_home_spread": 0.0,
            "sportsbook": "FanDuel",
        }
        for index in range(12)
    ]
    initial = update_season_state(None, 2026, {}, games, [], now, "refresh")
    assert len(initial["recommendations"]) == 10

    revised_games = [dict(game) for game in games]
    for game in revised_games:
        game["predicted_home_spread"] = -(
            0.5 + float(11 - int(game["event_id"])) * 0.1
        )
    refreshed = update_season_state(
        initial,
        2026,
        {},
        revised_games,
        [],
        datetime(2026, 10, 2, tzinfo=timezone.utc),
        "refresh",
    )

    assert len(refreshed["recommendations"]) == 10
    assert {
        pick["event_id"]: (pick["spread"], pick["sportsbook"], pick["difference"])
        for pick in refreshed["recommendations"]
    } == {
        pick["event_id"]: (pick["spread"], pick["sportsbook"], pick["difference"])
        for pick in initial["recommendations"]
    }


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


def test_refresh_does_not_replace_a_locked_line_with_a_newer_line():
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
    assert latest["recommendations"][0]["spread"] == -4
    assert latest["recommendations"][0]["sportsbook"] == "FanDuel"
    assert latest["recommendations"][0]["recommended_at"] == "2026-10-01T00:00:00+00:00"


def test_refresh_waits_for_prior_week_recommendations_to_be_graded():
    previous_kickoff = datetime(2026, 10, 5, 3, 30, tzinfo=timezone.utc)
    previous_game = {
        "event_id": "previous",
        "home_team": "Previous Home",
        "away_team": "Previous Away",
        "start_time": previous_kickoff.isoformat(),
        "predicted_home_spread": -8,
        "points_per_play_home_spread": -8,
        "elo_home_spread": 0.0,
        "market_home_spread": -4,
        "sportsbook": "FanDuel",
    }
    prior_state = update_season_state(
        None,
        2026,
        {},
        [previous_game],
        [],
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        "refresh",
        recommendation_week_start=date(2026, 9, 28),
    )
    current_games = [
        {
            "event_id": f"current-{index}",
            "home_team": f"Home {index}",
            "away_team": f"Away {index}",
            "start_time": datetime(2026, 10, 10, tzinfo=timezone.utc).isoformat(),
            "predicted_home_spread": -float(index + 1),
            "points_per_play_home_spread": -float(index + 1),
            "elo_home_spread": 0.0,
            "market_home_spread": 0.0,
            "sportsbook": "FanDuel",
        }
        for index in range(10)
    ]
    blocked = update_season_state(
        prior_state,
        2026,
        {},
        current_games,
        [],
        datetime(2026, 10, 7, tzinfo=timezone.utc),
        "refresh",
        recommendation_week_start=date(2026, 10, 5),
    )
    assert not any(
        item["event_id"].startswith("current-")
        for item in blocked["recommendations"]
    )

    completed = EloGame(
        event_id="previous",
        home_team="Previous Home",
        away_team="Previous Away",
        home_score=28,
        away_score=21,
        start_time=previous_kickoff,
    )
    graded = update_season_state(
        blocked,
        2026,
        {},
        [],
        [completed],
        datetime(2026, 10, 5, 23, tzinfo=timezone.utc),
        "grade",
    )
    refreshed = update_season_state(
        graded,
        2026,
        {},
        current_games,
        [],
        datetime(2026, 10, 7, tzinfo=timezone.utc),
        "refresh",
        recommendation_week_start=date(2026, 10, 5),
    )
    assert sum(
        item["start_time"].startswith("2026-10-10")
        for item in refreshed["recommendations"]
    ) == 10


def test_season_state_archives_and_grades_all_model_forecasts():
    kickoff = datetime(2026, 9, 5, 18, tzinfo=timezone.utc)
    game = {
        "event_id": "forecast-1",
        "home_team": "Home",
        "away_team": "Away",
        "start_time": kickoff.isoformat(),
        "predicted_home_spread": -3,
        "points_per_play_home_spread": -4,
        "elo_home_spread": -2,
        "market_home_spread": -3.5,
        "sportsbook": None,
    }
    saved = update_season_state(
        None,
        2026,
        {},
        [game],
        [],
        datetime(2026, 9, 4, tzinfo=timezone.utc),
        "refresh",
    )
    assert saved["model_history"][0]["status"] == "pending"
    assert saved["model_history"][0]["market_home_margin"] == 3.5
    assert len(saved["recommendations"]) == 1
    assert saved["recommendations"][0]["difference"] == pytest.approx(-0.5)

    graded = update_season_state(
        saved,
        2026,
        {},
        [],
        [
            EloGame(
                event_id="forecast-1",
                home_team="Home",
                away_team="Away",
                home_score=28,
                away_score=21,
                start_time=kickoff,
            )
        ],
        datetime(2026, 9, 6, 23, tzinfo=timezone.utc),
        "grade",
    )

    forecast = graded["model_history"][0]
    assert forecast["status"] == "completed"
    assert forecast["actual_home_margin"] == 7
    assert graded["model_calibration"]["status"] == "warming_up"
    assert graded["model_calibration"]["weekly_review"] == {
        "week_start": "2026-08-31",
        "completed_games": 1,
        "mae": 4.0,
    }


def test_weekly_grade_updates_parameters_only_after_validation_improves():
    history = []
    for index in range(100):
        points_margin = ((index * 17) % 43) - 21
        elo_margin = ((index * 11) % 19) - 9
        history.append(
            {
                "event_id": str(index),
                "home_team": "Home",
                "away_team": "Away",
                "start_time": datetime(
                    2026, 9, 1 + index // 10, tzinfo=timezone.utc
                ).isoformat(),
                "predicted_home_margin": (
                    0.5 * points_margin + 0.5 * elo_margin
                ),
                "points_per_play_home_margin": points_margin,
                "elo_home_margin": elo_margin,
                "actual_home_margin": (
                    3 + 1.1 * points_margin + 0.05 * elo_margin
                ),
                "status": "completed",
            }
        )
    existing = {
        "season": 2026,
        "recommendations": [],
        "model_history": history,
        "model_parameters": DEFAULT_MODEL_PARAMETERS.copy(),
    }

    reviewed = update_season_state(
        existing,
        2026,
        {},
        [],
        [],
        datetime(2026, 10, 4, 23, tzinfo=timezone.utc),
        "grade",
    )

    assert reviewed["model_calibration"]["status"] == "updated"
    assert reviewed["model_parameters"]["points_per_play_weight"] > 0.5
    assert reviewed["model_calibration"]["weekly_review"]["completed_games"] == 40
