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
        "ark": "arkansas",
        "boise": "boisestate",
        "cc": "coastalcarolina",
        "clem": "clemson",
        "colo": "colorado",
        "csu": "coloradostate",
        "coastalcar": "coastalcarolina",
        "ecarolina": "eastcarolina",
        "fau": "floridaatlantic",
        "floridaintl": "floridainternational",
        "gaso": "georgiasouthern",
        "georgiaso": "georgiasouthern",
        "jmadison": "jamesmadison",
        "lt": "louisianatech",
        "mcn": "mcneese",
        "mia": "miamifl",
        "miami": "miamifl",
        "miamifl": "miamifl",
        "miamiflorida": "miamifl",
        "miamioh": "miamioh",
        "miamiohio": "miamioh",
        "orst": "oregonstate",
        "olemiss": "mississippi",
        "mississippi": "mississippi",
        "taandm": "texasam",
        "tem": "temple",
        "ttu": "texastech",
        "txso": "texassouthern",
        "ulm": "louisianamonroe",
        "ulmonroe": "louisianamonroe",
        "usa": "southalabama",
        "usf": "southflorida",
        "uconn": "connecticut",
        "pitt": "pittsburgh",
        "southernmiss": "southernmississippi",
        "southernmississippi": "southernmississippi",
        "salabama": "southalabama",
        "sflorida": "southflorida",
        "usu": "utahstate",
        "texasam": "texasam",
        "texasamaggies": "texasam",
        "texasaandm": "texasam",
        "appstate": "appalachianstate",
        "appalachianstate": "appalachianstate",
        "cmichigan": "centralmichigan",
        "centralmichigan": "centralmichigan",
        "emichigan": "easternmichigan",
        "easternmichigan": "easternmichigan",
        "wash": "washington",
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
    model_intercept: float = 0.0
    points_per_play_weight: float = 0.5
    elo_weight: float = 0.5
    market_anchor_weight: float = 0.75

    @property
    def projected_home_margin(self) -> float:
        points_per_play_margin = self.home_points - self.away_points
        independent_margin = (
            self.model_intercept
            + self.points_per_play_weight * points_per_play_margin
            + self.elo_weight * self.elo_home_margin
        )
        if self.matchup.market_home_margin is None:
            return independent_margin
        return (
            (1 - self.market_anchor_weight) * independent_margin
            + self.market_anchor_weight * self.matchup.market_home_margin
        )
