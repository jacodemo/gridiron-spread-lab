from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .data import EloGame, team_key
from .model import DEFAULT_MODEL_PARAMETERS, calibrate_model_parameters

BET_THRESHOLD_POINTS = 3.0
MIN_WEEKLY_PICKS = 10
MAX_WEEKLY_PICKS = 15


def _bet_details(game: dict[str, Any]) -> dict[str, Any] | None:
    market_spread = game.get("market_home_spread")
    predicted_spread = game.get("predicted_home_spread")
    if market_spread is None or predicted_spread is None:
        return None
    difference = float(market_spread) - float(predicted_spread)
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


def recommended_bet(game: dict[str, Any]) -> dict[str, Any] | None:
    bet = _bet_details(game)
    if bet is None or abs(float(bet["difference"])) < BET_THRESHOLD_POINTS:
        return None
    return bet


def select_weekly_recommendations(
    games: list[dict[str, Any]],
    now: datetime,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for game in games:
        kickoff = datetime.fromisoformat(str(game["start_time"]).replace("Z", "+00:00"))
        if kickoff <= now:
            continue
        bet = _bet_details(game)
        if bet is not None:
            candidates.append((game, bet))

    candidates.sort(
        key=lambda candidate: (
            -abs(float(candidate[1]["difference"])),
            str(candidate[0]["start_time"]),
            str(candidate[0].get("event_id", "")),
        )
    )
    selected = candidates[:MIN_WEEKLY_PICKS]
    if len(selected) >= MIN_WEEKLY_PICKS:
        selected_keys = {_record_key(game) for game, _ in selected}
        for game, bet in candidates[MIN_WEEKLY_PICKS:]:
            if len(selected) >= MAX_WEEKLY_PICKS:
                break
            if (
                abs(float(bet["difference"])) >= BET_THRESHOLD_POINTS
                and _record_key(game) not in selected_keys
            ):
                selected.append((game, bet))
                selected_keys.add(_record_key(game))
    return selected


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
        "model_history": [],
        "model_parameters": DEFAULT_MODEL_PARAMETERS.copy(),
        "model_calibration": None,
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
    parameters = state.get("model_parameters")
    if not isinstance(parameters, dict):
        parameters = DEFAULT_MODEL_PARAMETERS.copy()
    else:
        parameters = {**DEFAULT_MODEL_PARAMETERS, **parameters}
    model_history: dict[str, dict[str, Any]] = {
        _record_key(item): item
        for item in state.get("model_history", [])
        if isinstance(item, dict)
    }
    scores_by_id = {game.event_id: game for game in completed_games}
    for item in model_history.values():
        event_id = item.get("event_id")
        final = scores_by_id.get(str(event_id)) if event_id is not None else None
        if item.get("status") != "pending" or final is None:
            continue
        if final.start_time < datetime.fromisoformat(
            str(item["start_time"]).replace("Z", "+00:00")
        ):
            raise ValueError(f"ESPN final event time precedes kickoff for {event_id}")
        item["actual_home_margin"] = final.home_score - final.away_score
        item["final_home_score"] = final.home_score
        item["final_away_score"] = final.away_score
        item["status"] = "completed"

    if mode == "refresh":
        selected_picks = {
            _record_key(game): bet
            for game, bet in select_weekly_recommendations(games, now)
        }
        refresh_slate_keys = {
            _record_key(game)
            for game in games
            if datetime.fromisoformat(
                str(game["start_time"]).replace("Z", "+00:00")
            ) > now
        }
        for key in refresh_slate_keys - selected_picks.keys():
            previous = recommendations.get(key)
            if previous is not None and previous.get("status") == "pending":
                del recommendations[key]

        for game in games:
            kickoff = datetime.fromisoformat(str(game["start_time"]).replace("Z", "+00:00"))
            if kickoff <= now:
                continue
            predicted_spread = game.get("predicted_home_spread")
            points_spread = game.get("points_per_play_home_spread")
            elo_spread = game.get("elo_home_spread")
            event_id = game.get("event_id")
            if (
                event_id is not None
                and isinstance(predicted_spread, (int, float))
                and isinstance(points_spread, (int, float))
                and isinstance(elo_spread, (int, float))
            ):
                key = _record_key(game)
                previous_forecast = model_history.get(key)
                if previous_forecast is None or previous_forecast.get("status") == "pending":
                    model_history[key] = {
                        "event_id": str(event_id),
                        "home_team": game["home_team"],
                        "away_team": game["away_team"],
                        "start_time": game["start_time"],
                        "predicted_home_margin": -float(predicted_spread),
                        "points_per_play_home_margin": -float(points_spread),
                        "elo_home_margin": -float(elo_spread),
                        "market_home_margin": (
                            -float(game["market_home_spread"])
                            if isinstance(game.get("market_home_spread"), (int, float))
                            else None
                        ),
                        "parameters": parameters.copy(),
                        "status": "pending",
                        "actual_home_margin": None,
                        "final_home_score": None,
                        "final_away_score": None,
                    }
            bet = selected_picks.get(_record_key(game))
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

    calibration: dict[str, Any] | None = state.get("model_calibration")
    if mode == "grade":
        completed_forecasts = [
            item for item in model_history.values()
            if item.get("status") == "completed"
        ]
        parameters, metrics = calibrate_model_parameters(
            completed_forecasts,
            parameters,
        )
        weekly_review = None
        if completed_forecasts:
            latest_game_date = max(
                datetime.fromisoformat(
                    str(item["start_time"]).replace("Z", "+00:00")
                ).date()
                for item in completed_forecasts
            )
            week_start = latest_game_date - timedelta(days=latest_game_date.weekday())
            latest_week = [
                item
                for item in completed_forecasts
                if (
                    datetime.fromisoformat(
                        str(item["start_time"]).replace("Z", "+00:00")
                    ).date()
                    - timedelta(
                        days=datetime.fromisoformat(
                            str(item["start_time"]).replace("Z", "+00:00")
                        ).date().weekday()
                    )
                )
                == week_start
            ]
            weekly_mae = sum(
                abs(
                    float(item["actual_home_margin"])
                    - float(item["predicted_home_margin"])
                )
                for item in latest_week
            ) / len(latest_week)
            weekly_review = {
                "week_start": week_start.isoformat(),
                "completed_games": len(latest_week),
                "mae": round(weekly_mae, 3),
            }
        calibration = {
            **metrics,
            "reviewed_at": now.isoformat(),
            "parameters": parameters.copy(),
            "weekly_review": weekly_review,
        }
    state["model_parameters"] = parameters
    state["model_calibration"] = calibration
    state["model_history"] = sorted(
        model_history.values(),
        key=lambda item: str(item["start_time"]),
    )
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
