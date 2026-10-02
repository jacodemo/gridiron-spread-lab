from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .data import EloGame, team_key

BET_THRESHOLD_POINTS = 3.0


def recommended_bet(game: dict[str, Any]) -> dict[str, Any] | None:
    market_spread = game.get("market_home_spread")
    if market_spread is None:
        return None
    difference = float(market_spread) - float(game["predicted_home_spread"])
    if abs(difference) < BET_THRESHOLD_POINTS:
        return None

    home_side = difference > 0
    team = game["home_team"] if home_side else game["away_team"]
    spread = float(market_spread) if home_side else -float(market_spread)
    return {
        "side": "home" if home_side else "away",
        "team": team,
        "spread": round(spread, 1),
        "difference": round(difference, 1),
        "sportsbook": game.get("sportsbook"),
    }


def _record_key(game: dict[str, Any]) -> str:
    event_id = game.get("event_id")
    if event_id:
        return str(event_id)
    return "|".join(
        (
            str(game["start_time"]),
            team_key(str(game["home_team"])),
            team_key(str(game["away_team"])),
        )
    )


def _new_state(season: int) -> dict[str, Any]:
    return {
        "season": season,
        "updated_at": None,
        "elo_ratings": {},
        "recommendations": [],
    }


def update_season_state(
    existing: dict[str, Any] | None,
    season: int,
    elo_ratings: dict[str, float],
    games: list[dict[str, Any]],
    completed_games: list[EloGame],
    now: datetime,
    mode: str,
) -> dict[str, Any]:
    state = (
        existing
        if existing is not None and existing.get("season") == season
        else _new_state(season)
    )
    state["season"] = season
    state["updated_at"] = now.isoformat()
    state["elo_ratings"] = {
        name: round(rating, 2) for name, rating in sorted(elo_ratings.items())
    }
    recommendations: dict[str, dict[str, Any]] = {
        _record_key(item): item
        for item in state.get("recommendations", [])
        if isinstance(item, dict)
    }

    if mode == "refresh":
        for game in games:
            kickoff = datetime.fromisoformat(str(game["start_time"]).replace("Z", "+00:00"))
            if kickoff <= now:
                continue
            bet = recommended_bet(game)
            if bet is None:
                continue
            key = _record_key(game)
            previous = recommendations.get(key)
            if previous is not None and previous.get("status") != "pending":
                continue
            recommendations[key] = {
                "event_id": game.get("event_id"),
                "home_team": game["home_team"],
                "away_team": game["away_team"],
                "start_time": game["start_time"],
                "side": bet["side"],
                "team": bet["team"],
                "spread": bet["spread"],
                "home_spread": game["market_home_spread"],
                "difference": bet["difference"],
                "sportsbook": bet["sportsbook"],
                "recommended_at": now.isoformat(),
                "status": "pending",
                "final_home_score": None,
                "final_away_score": None,
            }
    elif mode != "grade":
        raise ValueError(f"Unsupported season-record update mode: {mode}")

    scores_by_id = {game.event_id: game for game in completed_games}
    for item in recommendations.values():
        event_id = item.get("event_id")
        final = scores_by_id.get(str(event_id)) if event_id is not None else None
        if item.get("status") != "pending" or final is None:
            continue
        if final.start_time < datetime.fromisoformat(
            str(item["start_time"]).replace("Z", "+00:00")
        ):
            raise ValueError(f"ESPN final event time precedes kickoff for {event_id}")
        home_cover_margin = final.home_score - final.away_score + float(item["home_spread"])
        if item["side"] == "home":
            cover_margin = home_cover_margin
        else:
            cover_margin = -home_cover_margin
        item["status"] = (
            "win" if cover_margin > 0 else "loss" if cover_margin < 0 else "push"
        )
        item["final_home_score"] = final.home_score
        item["final_away_score"] = final.away_score
        item["graded_at"] = now.isoformat()

    state["recommendations"] = sorted(
        recommendations.values(),
        key=lambda item: str(item["start_time"]),
    )
    return state


def season_record_summary(state: dict[str, Any]) -> dict[str, int]:
    recommendations = state.get("recommendations", [])
    return {
        "wins": sum(item.get("status") == "win" for item in recommendations),
        "losses": sum(item.get("status") == "loss" for item in recommendations),
        "pushes": sum(item.get("status") == "push" for item in recommendations),
        "pending": sum(item.get("status") == "pending" for item in recommendations),
    }


def empty_season_state(season: int) -> dict[str, Any]:
    return _new_state(season)


def season_start_date(season: int) -> date:
    return date(season, 8, 1)
