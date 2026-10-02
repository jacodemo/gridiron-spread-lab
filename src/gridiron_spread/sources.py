from __future__ import annotations

import json
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

    selection = next(
        (
            (named[name], {"fanduel": "FanDuel", "draftkings": "DraftKings"}[name])
            for name in ("fanduel", "draftkings")
            if name in named
        ),
        None,
    )
    if selection is None:
        return None, None

    book_odds, provider_name = selection
    home_odds = book_odds.get("homeTeamOdds")
    spread = book_odds.get("spread")
    if not isinstance(home_odds, dict) or spread is None or home_odds.get("favorite") is None:
        return None, provider_name
    magnitude = abs(float(spread))
    home_margin = magnitude if bool(home_odds["favorite"]) else -magnitude
    return home_margin, provider_name


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

    return matchups


def current_season_year(reference_date: date | None = None) -> int:
    reference_date = reference_date or datetime.now(timezone.utc).date()
    return reference_date.year if reference_date.month >= 7 else reference_date.year - 1
