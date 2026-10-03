from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .data import EloGame, Matchup, TeamStats, team_key

TEAMRANKINGS_URLS = {
    "offensive_points_per_play": "https://www.teamrankings.com/college-football/stat/points-per-play",
    "offensive_plays_per_game": "https://www.teamrankings.com/college-football/stat/plays-per-game",
    "opponent_plays_per_game": "https://www.teamrankings.com/college-football/stat/opponent-plays-per-game",
    "opponent_points_per_play": "https://www.teamrankings.com/college-football/stat/opponent-points-per-play",
}
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams"
SPORTSLINE_ODDS_URL = "https://www.sportsline.com/college-football/odds/"
USER_AGENT = "GridironSpreadLab/0.1 (+college football matchup analysis)"
ELO_K_FACTOR = 20


def _fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except (OSError, URLError) as error:
        raise RuntimeError(f"Unable to fetch live data from {url}: {error}") from error


class _StatsTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.rows: list[list[tuple[str, str]]] = []
        self.row: list[tuple[str, str]] = []
        self.cell_text: list[str] = []
        self.cell_sort_value = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and "tr-table" in attributes.get("class", "").split():
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row = True
            self.row = []
        elif self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.cell_text = []
            self.cell_sort_value = attributes.get("data-sort") or ""

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_cell and tag in {"th", "td"}:
            text = " ".join("".join(self.cell_text).split())
            self.row.append((self.cell_sort_value or text, text))
            self.in_cell = False
        elif self.in_row and tag == "tr":
            self.rows.append(self.row)
            self.in_row = False
        elif tag == "table" and self.in_table:
            self.in_table = False


def _read_team_stat(
    html: str,
    season_year: int,
    source_url: str,
) -> dict[str, tuple[str, float]]:
    parser = _StatsTableParser()
    parser.feed(html)
    if not parser.rows:
        raise ValueError(f"No TeamRankings stats table found at {source_url}")

    season_column: int | None = None
    for row in parser.rows:
        headers = [text for _, text in row]
        for index, heading in enumerate(headers):
            if heading.strip() == str(season_year):
                season_column = index
                break
        if season_column is not None:
            break
    if season_column is None:
        raise ValueError(f"No {season_year} season column found at {source_url}")

    stats: dict[str, tuple[str, float]] = {}
    for row in parser.rows:
        if len(row) <= season_column or len(row) < 3:
            continue
        team = row[1][1].strip()
        if not team or team.casefold() == "team":
            continue
        try:
            value = float(row[season_column][0].replace(",", ""))
        except ValueError:
            continue
        stats[team_key(team)] = (team, value)

    if not stats:
        raise ValueError(f"No team rows were parsed from {source_url}")
    return stats


def fetch_team_stats(season_year: int | None = None) -> dict[str, TeamStats]:
    if season_year is None:
        season_year = date.today().year

    stat_rows = {
        stat: _read_team_stat(_fetch_text(url), season_year, url)
        for stat, url in TEAMRANKINGS_URLS.items()
    }
    team_keys = set.intersection(*(set(rows) for rows in stat_rows.values()))
    if not team_keys:
        raise ValueError(f"No overlapping team rows found for the {season_year} season")

    stats: dict[str, TeamStats] = {}
    for key in sorted(team_keys):
        team = stat_rows["offensive_points_per_play"][key][0]
        stats[team] = TeamStats(
            team=team,
            offensive_points_per_play=stat_rows["offensive_points_per_play"][key][1],
            offensive_plays_per_game=stat_rows["offensive_plays_per_game"][key][1],
            opponent_plays_per_game=stat_rows["opponent_plays_per_game"][key][1],
            opponent_points_per_play=stat_rows["opponent_points_per_play"][key][1],
        )
    return stats


def _canonical_team_name(team: dict[str, object]) -> str:
    abbreviation = str(team.get("abbreviation", "")).upper()
    if abbreviation == "MIA":
        return "Miami FL"
    if abbreviation == "M-OH":
        return "Miami OH"
    location = str(team.get("location") or team.get("displayName") or "")
    if not location:
        raise ValueError(f"ESPN team entry is missing its name: {team!r}")
    return location


def _fetch_scoreboard_day(game_date: date) -> list[dict[str, object]]:
    params = urlencode(
        {
            "groups": "80",
            "limit": "1000",
            "dates": game_date.strftime("%Y%m%d"),
            "region": "us",
        }
    )
    payload = json.loads(_fetch_text(f"{ESPN_SCOREBOARD_URL}?{params}"))
    events = payload.get("events", [])
    if not isinstance(events, list):
        raise ValueError(f"ESPN scoreboard returned an invalid event list for {game_date}")
    return [event for event in events if isinstance(event, dict)]


def fetch_scoreboard_events(start_date: date, end_date: date) -> list[dict[str, object]]:
    if end_date < start_date:
        raise ValueError("Scoreboard end date must not precede start date")
    dates = [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]
    with ThreadPoolExecutor(max_workers=8) as executor:
        daily_events = list(executor.map(_fetch_scoreboard_day, dates))
    unique_events = {
        str(event["id"]): event
        for events in daily_events
        for event in events
        if event.get("id") is not None
    }
    return sorted(
        unique_events.values(),
        key=lambda event: str(event.get("date", "")),
    )


def _fetch_team_schedule_events(
    team_id: str,
    season_year: int,
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    params = urlencode({"season": str(season_year), "seasontype": "2"})
    payload = json.loads(
        _fetch_text(f"{ESPN_TEAMS_URL}/{team_id}/schedule?{params}")
    )
    events = payload.get("events", [])
    if not isinstance(events, list):
        raise ValueError(f"ESPN returned an invalid team schedule for team {team_id}")

    week_events: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("date"), str):
            continue
        event_date = datetime.fromisoformat(
            str(event["date"]).replace("Z", "+00:00")
        ).date()
        if start_date <= event_date <= end_date:
            week_events.append(event)
    return week_events


def _fetch_supplemental_team_events(
    scheduled_events: list[dict[str, object]],
    team_names: Iterable[str],
    season_year: int,
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    scheduled_team_keys: set[str] = set()
    for event in scheduled_events:
        competitions = event.get("competitions")
        if not isinstance(competitions, list) or not competitions:
            continue
        competition = competitions[0]
        if not isinstance(competition, dict):
            continue
        competitors = competition.get("competitors")
        if not isinstance(competitors, list):
            continue
        for competitor in competitors:
            team = competitor.get("team") if isinstance(competitor, dict) else None
            if isinstance(team, dict):
                scheduled_team_keys.add(team_key(_canonical_team_name(team)))
    missing_team_keys = {
        team_key(name) for name in team_names
    } - scheduled_team_keys
    if not missing_team_keys:
        return []

    params = urlencode({"limit": "1000"})
    payload = json.loads(_fetch_text(f"{ESPN_TEAMS_URL}?{params}"))
    sports = payload.get("sports")
    if not isinstance(sports, list) or not sports:
        raise ValueError("ESPN returned an invalid college-football team index")
    leagues = sports[0].get("leagues") if isinstance(sports[0], dict) else None
    if not isinstance(leagues, list) or not leagues:
        raise ValueError("ESPN team index did not include a football league")
    entries = leagues[0].get("teams") if isinstance(leagues[0], dict) else None
    if not isinstance(entries, list):
        raise ValueError("ESPN team index did not include its team list")

    team_ids: set[str] = set()
    matched_team_keys: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        team = entry.get("team")
        if not isinstance(team, dict) or team.get("isActive") is False:
            continue
        team_key_candidates = {
            team_key(str(team[field]))
            for field in ("location", "displayName", "shortDisplayName", "abbreviation")
            if team.get(field)
        }
        matched_keys = team_key_candidates & missing_team_keys
        team_id = team.get("id")
        if matched_keys and team_id is not None:
            team_ids.add(str(team_id))
            matched_team_keys.update(matched_keys)

    unmatched_team_keys = missing_team_keys - matched_team_keys
    if unmatched_team_keys:
        raise ValueError(
            "ESPN team index is missing active TeamRankings teams: "
            + ", ".join(sorted(unmatched_team_keys))
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        schedule_lists = list(
            executor.map(
                lambda team_id: _fetch_team_schedule_events(
                    team_id,
                    season_year,
                    start_date,
                    end_date,
                ),
                sorted(team_ids),
            )
        )

    events_by_id = {
        str(event["id"]): event
        for events in schedule_lists
        for event in events
        if event.get("id") is not None
    }
    return sorted(events_by_id.values(), key=lambda event: str(event.get("date", "")))


def completed_elo_games(events: list[dict[str, object]]) -> list[EloGame]:
    games: list[EloGame] = []
    for event in events:
        event_id = event.get("id")
        competitions = event.get("competitions")
        if event_id is None or not isinstance(competitions, list) or not competitions:
            continue
        competition = competitions[0]
        if not isinstance(competition, dict):
            continue
        status = competition.get("status")
        status_type = status.get("type") if isinstance(status, dict) else None
        if not isinstance(status_type, dict) or status_type.get("state") != "post":
            continue
        competitors = competition.get("competitors")
        if not isinstance(competitors, list):
            continue
        by_side = {
            competitor.get("homeAway"): competitor
            for competitor in competitors
            if isinstance(competitor, dict)
        }
        home = by_side.get("home")
        away = by_side.get("away")
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue
        home_team = home.get("team")
        away_team = away.get("team")
        if not isinstance(home_team, dict) or not isinstance(away_team, dict):
            continue
        try:
            home_score = int(home["score"])
            away_score = int(away["score"])
            start_time = datetime.fromisoformat(
                str(event["date"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            continue
        games.append(
            EloGame(
                event_id=str(event_id),
                home_team=_canonical_team_name(home_team),
                away_team=_canonical_team_name(away_team),
                home_score=home_score,
                away_score=away_score,
                start_time=start_time,
            )
        )
    return sorted(games, key=lambda game: (game.start_time, game.event_id))


def calculate_elo_ratings(games: list[EloGame]) -> dict[str, float]:
    ratings: dict[str, float] = {}
    for game in sorted(games, key=lambda item: (item.start_time, item.event_id)):
        home_key = team_key(game.home_team)
        away_key = team_key(game.away_team)
        home_rating = ratings.get(home_key, 1500.0)
        away_rating = ratings.get(away_key, 1500.0)
        expected_home = 1 / (1 + 10 ** ((away_rating - home_rating) / 400))
        actual_home = (
            1.0
            if game.home_score > game.away_score
            else 0.0
            if game.home_score < game.away_score
            else 0.5
        )
        change = ELO_K_FACTOR * (actual_home - expected_home)
        ratings[home_key] = home_rating + change
        ratings[away_key] = away_rating - change
    return ratings


def fetch_season_elo_ratings(
    season_year: int,
    through_date: date | None = None,
) -> dict[str, float]:
    through_date = through_date or date.today()
    season_start = date(season_year, 8, 1)
    if through_date < season_start:
        return {}
    events = fetch_scoreboard_events(season_start, through_date)
    return calculate_elo_ratings(completed_elo_games(events))


def _odds_home_spread(odds: dict[str, object]) -> float | None:
    home_odds = odds.get("homeTeamOdds")
    spread = odds.get("spread")
    if not isinstance(home_odds, dict) or spread is None or home_odds.get("favorite") is None:
        return None
    try:
        magnitude = abs(float(spread))
    except (TypeError, ValueError):
        return None
    return -magnitude if bool(home_odds["favorite"]) else magnitude


def _espn_market_lines(competition: dict[str, object]) -> dict[str, float]:
    odds = competition.get("odds")
    if not isinstance(odds, list):
        return {}

    lines: dict[str, float] = {}
    feed_line: float | None = None
    for item in odds:
        if not isinstance(item, dict):
            continue
        home_spread = _odds_home_spread(item)
        if home_spread is None:
            continue
        if feed_line is None:
            feed_line = home_spread
        provider = item.get("provider")
        if not isinstance(provider, dict):
            continue
        name = str(provider.get("name", "")).casefold()
        if name in {"fanduel", "draftkings"}:
            provider_name = {"fanduel": "FanDuel", "draftkings": "DraftKings"}[name]
            lines[provider_name] = home_spread
        elif name == "espn":
            lines["ESPN"] = home_spread

    if feed_line is not None:
        lines["ESPN"] = lines.get("ESPN", feed_line)
    return lines


def _espn_odds(competition: dict[str, object]) -> tuple[float | None, str | None]:
    lines = _espn_market_lines(competition)
    for name in ("FanDuel", "DraftKings", "ESPN"):
        if name in lines:
            return -lines[name], name
    return None, None


def _sportsline_apollo_state(html: str) -> dict[str, object]:
    marker = '"apolloState":'
    decoder = json.JSONDecoder()
    for match in re.finditer(r"self\.__next_f\.push\(\[1,", html):
        try:
            flight_data, _ = decoder.raw_decode(html[match.end():])
        except json.JSONDecodeError:
            continue
        if not isinstance(flight_data, str) or marker not in flight_data:
            continue
        state_start = flight_data.index(marker) + len(marker)
        state, _ = decoder.raw_decode(flight_data[state_start:])
        if isinstance(state, dict):
            return state
    raise ValueError(f"No SportsLine odds data found at {SPORTSLINE_ODDS_URL}")


def _sportsline_spreads(
    state: dict[str, object],
) -> dict[tuple[str, str, date], dict[str, float]]:
    root_query = state.get("ROOT_QUERY")
    if not isinstance(root_query, dict):
        raise ValueError("SportsLine odds data did not include its event index")

    event_lists = [
        value.get("oddsCompetitions")
        for key, value in root_query.items()
        if key.startswith("odds(") and isinstance(value, dict)
    ]
    event_refs = max(
        (refs for refs in event_lists if isinstance(refs, list)),
        key=len,
        default=[],
    )
    if not event_refs:
        raise ValueError("SportsLine odds data did not include college football matchups")

    spreads: dict[tuple[str, str, date], dict[str, float]] = {}
    for event_ref in event_refs:
        if not isinstance(event_ref, dict):
            continue
        event_key = event_ref.get("__ref")
        competition = state.get(event_key) if isinstance(event_key, str) else None
        if not isinstance(competition, dict):
            continue

        home_id = competition.get("homeTeamId")
        away_id = competition.get("awayTeamId")
        scheduled_time = competition.get("scheduledTime")
        if not isinstance(scheduled_time, str):
            continue
        game_date = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00")).date()
        home_team = state.get(f"CompetitionDTOTeam:{home_id}")
        away_team = state.get(f"CompetitionDTOTeam:{away_id}")
        if not isinstance(home_team, dict) or not isinstance(away_team, dict):
            continue

        home_name = str(home_team.get("location") or home_team.get("mediumName") or "")
        away_name = str(away_team.get("location") or away_team.get("mediumName") or "")
        if not home_name or not away_name:
            continue

        book_odds = competition.get("sportsBookOdds")
        if not isinstance(book_odds, dict):
            continue
        lines: dict[str, float] = {}
        for book, label in (
            ("fanduel", "FanDuel"),
            ("draftkings", "DraftKings"),
            ("consensus", "SportsLine"),
        ):
            odds = book_odds.get(book)
            spread = odds.get("spread") if isinstance(odds, dict) else None
            home_line = spread.get("home") if isinstance(spread, dict) else None
            value = home_line.get("value") if isinstance(home_line, dict) else None
            if not isinstance(value, str):
                continue
            line_match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*", value)
            if line_match:
                lines[label] = float(line_match.group(1))
        if lines:
            spreads[(team_key(home_name), team_key(away_name), game_date)] = lines

    return spreads


def fetch_sportsline_spreads(
    html: str | None = None,
) -> dict[tuple[str, str, date], dict[str, float]]:
    if html is None:
        html = _fetch_text(SPORTSLINE_ODDS_URL)
    return _sportsline_spreads(_sportsline_apollo_state(html))


def fetch_weekly_matchups(
    reference_date: date | None = None,
    team_names: Iterable[str] | None = None,
) -> list[Matchup]:
    reference_date = reference_date or date.today()
    week_start = reference_date - timedelta(days=reference_date.weekday())
    week_end = week_start + timedelta(days=6)
    events = fetch_scoreboard_events(week_start, week_end)
    if team_names is not None:
        supplemental_events = _fetch_supplemental_team_events(
            events,
            team_names,
            current_season_year(reference_date),
            week_start,
            week_end,
        )
        events_by_id = {
            str(event["id"]): event
            for event in supplemental_events
            if event.get("id") is not None
        }
        events_by_id.update(
            {
                str(event["id"]): event
                for event in events
                if event.get("id") is not None
            }
        )
        events = sorted(
            events_by_id.values(),
            key=lambda event: str(event.get("date", "")),
        )

    matchups: list[Matchup] = []
    for event in events:
        competitions = event.get("competitions", [])
        if not competitions:
            continue
        competition = competitions[0]
        competitors = competition.get("competitors", [])
        by_home_away = {
            competitor.get("homeAway"): competitor.get("team", {})
            for competitor in competitors
            if isinstance(competitor, dict)
        }
        home = by_home_away.get("home")
        away = by_home_away.get("away")
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue

        start_time = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
        market_lines = _espn_market_lines(competition)
        market_margin, sportsbook = _espn_odds(competition)
        market_spreads = market_lines.copy()
        matchups.append(
            Matchup(
                home_team=_canonical_team_name(home),
                away_team=_canonical_team_name(away),
                start_time=start_time,
                market_home_margin=market_margin,
                sportsbook=sportsbook,
                event_id=str(event["id"]),
                market_lines=market_spreads,
            )
        )

    sportsline_spreads = fetch_sportsline_spreads() if matchups else {}
    provider_priority = ("FanDuel", "DraftKings", "ESPN", "SportsLine")
    completed_matchups: list[Matchup] = []
    for matchup in matchups:
        lines = matchup.market_lines.copy()
        sportsline_lines = sportsline_spreads.get(
            (
                team_key(matchup.home_team),
                team_key(matchup.away_team),
                matchup.start_time.date(),
            ),
            {},
        )
        for name, value in sportsline_lines.items():
            lines.setdefault(name, value)
        selected_source = next((name for name in provider_priority if name in lines), None)
        selected_home_spread = lines.get(selected_source) if selected_source else None
        completed_matchups.append(
            replace(
                matchup,
                market_lines=lines,
                market_home_margin=(
                    -selected_home_spread if selected_home_spread is not None else None
                ),
                sportsbook=selected_source,
            )
        )

    return completed_matchups


def current_season_year(reference_date: date | None = None) -> int:
    reference_date = reference_date or datetime.now(timezone.utc).date()
    return reference_date.year if reference_date.month >= 7 else reference_date.year - 1
