import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime


def team_key(team: str) -> str:
    name = team.casefold().replace("&", "and")
    name = "".join(
        character
        for character in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(character)
    )
    name = re.sub(r"\bst\.?\b", "state", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    aliases = {
        "coastalcar": "coastalcarolina",
        "ecarolina": "eastcarolina",
        "floridaintl": "floridainternational",
        "georgiaso": "georgiasouthern",
        "jmadison": "jamesmadison",
        "miami": "miamifl",
        "miamifl": "miamifl",
        "miamiflorida": "miamifl",
        "miamioh": "miamioh",
        "miamiohio": "miamioh",
        "olemiss": "mississippi",
        "mississippi": "mississippi",
        "uconn": "connecticut",
        "pitt": "pittsburgh",
        "southernmiss": "southernmississippi",
        "southernmississippi": "southernmississippi",
        "salabama": "southalabama",
        "sflorida": "southflorida",
        "texasam": "texasam",
        "texasamaggies": "texasam",
        "appstate": "appalachianstate",
        "appalachianstate": "appalachianstate",
        "cmichigan": "centralmichigan",
        "centralmichigan": "centralmichigan",
        "emichigan": "easternmichigan",
        "easternmichigan": "easternmichigan",
        "umass": "massachusetts",
        "massachusetts": "massachusetts",
        "middletenn": "middletennessee",
        "middletennessee": "middletennessee",
        "ntexas": "northtexas",
        "northtexas": "northtexas",
        "wkentucky": "westernkentucky",
        "westernkentucky": "westernkentucky",
        "wmichigan": "westernmichigan",
        "westernmichigan": "westernmichigan",
    }
    return aliases.get(name, name)


@dataclass(frozen=True)
class TeamStats:
    team: str
    offensive_points_per_play: float
    offensive_plays_per_game: float
    opponent_plays_per_game: float
    opponent_points_per_play: float

    @property
    def offensive_points_per_game(self) -> float:
        return self.offensive_points_per_play * self.offensive_plays_per_game

    @property
    def opponent_points_per_game(self) -> float:
        return self.opponent_points_per_play * self.opponent_plays_per_game


@dataclass(frozen=True)
class Matchup:
    home_team: str
    away_team: str
    start_time: datetime
    market_home_margin: float | None
    sportsbook: str | None
    event_id: str | None = None
    market_lines: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class EloGame:
    event_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    start_time: datetime


@dataclass(frozen=True)
class Projection:
    matchup: Matchup
    home_points: float
    away_points: float
    elo_home_margin: float = 0.0

    @property
    def projected_home_margin(self) -> float:
        points_per_play_margin = self.home_points - self.away_points
        return (points_per_play_margin + self.elo_home_margin) / 2
