from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .data import Matchup, TeamStats, team_key

TEAMRANKINGS_URLS = {
    "offensive_points_per_play": "https://www.teamrankings.com/college-football/stat/points-per-play",
    "offensive_plays_per_game": "https://www.teamrankings.com/college-football/stat/plays-per-game",
    "opponent_plays_per_game": "https://www.teamrankings.com/college-football/stat/opponent-plays-per-game",
    "opponent_points_per_play": "https://www.teamrankings.com/college-football/stat/opponent-points-per-play",
}
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
SPORTSLINE_ODDS_URL = "https://www.sportsline.com/college-football/odds/"
USER_AGENT = "GridironSpreadLab/0.1 (+college football matchup analysis)"


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


def _espn_odds(competition: dict[str, object]) -> tuple[float | None, str | None]:
    odds = competition.get("odds")
    if not isinstance(odds, list):
        return None, None

    named: dict[str, dict[str, object]] = {}
    for item in odds:
        if isinstance(item, dict):
            provider = item.get("provider")
            if isinstance(provider, dict) and provider.get("name"):
                named[str(provider["name"]).casefold()] = item

    for name, provider_name in (("fanduel", "FanDuel"), ("draftkings", "DraftKings")):
        book_odds = named.get(name)
        if book_odds is None:
            continue
        home_odds = book_odds.get("homeTeamOdds")
        spread = book_odds.get("spread")
        if not isinstance(home_odds, dict) or spread is None or home_odds.get("favorite") is None:
            continue
        try:
            magnitude = abs(float(spread))
        except (TypeError, ValueError):
            continue
        home_margin = magnitude if bool(home_odds["favorite"]) else -magnitude
        return home_margin, provider_name
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


def _sportsline_spreads(state: dict[str, object]) -> dict[tuple[str, str, date], float]:
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

    spreads: dict[tuple[str, str, date], float] = {}
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
        consensus = book_odds.get("consensus")
        spread = consensus.get("spread") if isinstance(consensus, dict) else None
        home_line = spread.get("home") if isinstance(spread, dict) else None
        value = home_line.get("value") if isinstance(home_line, dict) else None
        if not isinstance(value, str):
            continue
        line_match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*", value)
        if not line_match:
            continue

        home_spread = float(line_match.group(1))
        spreads[(team_key(home_name), team_key(away_name), game_date)] = -home_spread

    return spreads


def fetch_sportsline_spreads(html: str | None = None) -> dict[tuple[str, str, date], float]:
    if html is None:
        html = _fetch_text(SPORTSLINE_ODDS_URL)
    return _sportsline_spreads(_sportsline_apollo_state(html))


def fetch_weekly_matchups(reference_date: date | None = None) -> list[Matchup]:
    reference_date = reference_date or date.today()
    week_start = reference_date - timedelta(days=reference_date.weekday())
    week_end = week_start + timedelta(days=6)
    events: list[dict[str, object]] = []
    for day_offset in range(7):
        game_date = week_start + timedelta(days=day_offset)
        params = urlencode(
            {
                "groups": "80",
                "limit": "1000",
                "dates": game_date.strftime("%Y%m%d"),
                "region": "us",
            }
        )
        payload = json.loads(_fetch_text(f"{ESPN_SCOREBOARD_URL}?{params}"))
        events.extend(payload.get("events", []))

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
        market_margin, sportsbook = _espn_odds(competition)
        matchups.append(
            Matchup(
                home_team=_canonical_team_name(home),
                away_team=_canonical_team_name(away),
                start_time=start_time,
                market_home_margin=market_margin,
                sportsbook=sportsbook,
            )
        )

    if any(matchup.market_home_margin is None for matchup in matchups):
        sportsline_spreads = fetch_sportsline_spreads()
        matchups = [
            replace(
                matchup,
                market_home_margin=sportsline_spreads[
                    (
                        team_key(matchup.home_team),
                        team_key(matchup.away_team),
                        matchup.start_time.date(),
                    )
                ],
                sportsbook="SportsLine",
            )
            if (
                matchup.market_home_margin is None
                and (
                    team_key(matchup.home_team),
                    team_key(matchup.away_team),
                    matchup.start_time.date(),
                )
                in sportsline_spreads
            )
            else matchup
            for matchup in matchups
        ]

    return matchups


def current_season_year(reference_date: date | None = None) -> int:
    reference_date = reference_date or datetime.now(timezone.utc).date()
    return reference_date.year if reference_date.month >= 7 else reference_date.year - 1
