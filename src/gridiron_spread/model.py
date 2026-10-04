from __future__ import annotations

from .data import Matchup, Projection, TeamStats, team_key

ELO_POINTS_PER_RATING = 25
MIN_CALIBRATION_GAMES = 40
MIN_REGRESSION_GAMES = 20
DEFAULT_MODEL_PARAMETERS = {
    "intercept": 0.0,
    "points_per_play_weight": 0.5,
    "elo_weight": 0.5,
    "market_anchor_weight": 0.70,
}


def predict_team_points(team_stats: TeamStats, opponent_stats: TeamStats) -> float:
    return (team_stats.offensive_points_per_game + opponent_stats.opponent_points_per_game) / 2


def project_matchup(
    matchup: Matchup,
    team_stats: dict[str, TeamStats],
    elo_ratings: dict[str, float] | None = None,
    model_parameters: dict[str, float] | None = None,
) -> Projection:
    stats_by_key = {team_key(name): stats for name, stats in team_stats.items()}
    home_stats = stats_by_key.get(team_key(matchup.home_team))
    away_stats = stats_by_key.get(team_key(matchup.away_team))
    missing = [
        team
        for team, stats in (
            (matchup.home_team, home_stats),
            (matchup.away_team, away_stats),
        )
        if stats is None
    ]
    if missing:
        raise ValueError(f"Missing TeamRankings statistics for {', '.join(missing)}")

    ratings_by_key = {
        team_key(name): rating for name, rating in (elo_ratings or {}).items()
    }
    home_rating = ratings_by_key.get(team_key(matchup.home_team), 1500.0)
    away_rating = ratings_by_key.get(team_key(matchup.away_team), 1500.0)
    parameters = model_parameters or DEFAULT_MODEL_PARAMETERS

    return Projection(
        matchup=matchup,
        home_points=predict_team_points(home_stats, away_stats),
        away_points=predict_team_points(away_stats, home_stats),
        elo_home_margin=(home_rating - away_rating) / ELO_POINTS_PER_RATING,
        model_intercept=float(parameters["intercept"]),
        points_per_play_weight=float(parameters["points_per_play_weight"]),
        elo_weight=float(parameters["elo_weight"]),
        market_anchor_weight=float(
            parameters.get(
                "market_anchor_weight",
                DEFAULT_MODEL_PARAMETERS["market_anchor_weight"],
            )
        ),
    )


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    size = len(vector)
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("Calibration regression is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                current - factor * pivot_value
                for current, pivot_value in zip(augmented[row], augmented[column])
            ]
    return [augmented[row][-1] for row in range(size)]


def fit_model_parameters(
    records: list[dict[str, object]],
    prior: dict[str, float] | None = None,
) -> dict[str, float]:
    if len(records) < MIN_REGRESSION_GAMES:
        raise ValueError(
            f"At least {MIN_REGRESSION_GAMES} completed forecasts are required"
        )

    prior_parameters = {**DEFAULT_MODEL_PARAMETERS, **(prior or {})}
    market_anchor_weight = float(prior_parameters["market_anchor_weight"])
    if not 0 <= market_anchor_weight < 1:
        raise ValueError("Market anchor weight must be between 0 (inclusive) and 1")
    penalties = [100.0, 25.0, 25.0]
    prior_values = [
        float(prior_parameters["intercept"]),
        float(prior_parameters["points_per_play_weight"]),
        float(prior_parameters["elo_weight"]),
    ]
    matrix = [[0.0] * 3 for _ in range(3)]
    vector = [0.0] * 3
    for record in records:
        features = [
            1.0,
            float(record["points_per_play_home_margin"]),
            float(record["elo_home_margin"]),
        ]
        target = float(record["actual_home_margin"])
        market_margin = record.get("market_home_margin")
        if isinstance(market_margin, (int, float)):
            target = (
                target - market_anchor_weight * float(market_margin)
            ) / (1 - market_anchor_weight)
        for row in range(3):
            vector[row] += features[row] * target
            for column in range(3):
                matrix[row][column] += features[row] * features[column]

    for index, penalty in enumerate(penalties):
        matrix[index][index] += penalty
        vector[index] += penalty * prior_values[index]
    intercept, points_weight, elo_weight = _solve_linear_system(matrix, vector)
    return {
        "intercept": round(min(10.0, max(-10.0, intercept)), 4),
        "points_per_play_weight": round(min(1.5, max(0.0, points_weight)), 4),
        "elo_weight": round(min(1.5, max(0.0, elo_weight)), 4),
        "market_anchor_weight": market_anchor_weight,
    }


def calibrate_model_parameters(
    records: list[dict[str, object]],
    current: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, object]]:
    parameters = {**DEFAULT_MODEL_PARAMETERS, **(current or {})}
    completed = sorted(records, key=lambda record: str(record["start_time"]))
    count = len(completed)
    if count < MIN_CALIBRATION_GAMES:
        return parameters, {
            "status": "warming_up",
            "observations": count,
            "minimum_observations": MIN_CALIBRATION_GAMES,
        }

    validation_count = min(20, max(10, count // 5))
    validation_start = count - validation_count
    baseline_errors: list[float] = []
    calibrated_errors: list[float] = []
    for index in range(validation_start, count):
        training = completed[:index]
        if len(training) < MIN_REGRESSION_GAMES:
            continue
        candidate = fit_model_parameters(training, parameters)
        record = completed[index]
        target = float(record["actual_home_margin"])
        points_margin = float(record["points_per_play_home_margin"])
        elo_margin = float(record["elo_home_margin"])
        baseline_independent_prediction = (
            parameters["intercept"]
            + parameters["points_per_play_weight"] * points_margin
            + parameters["elo_weight"] * elo_margin
        )
        candidate_independent_prediction = (
            candidate["intercept"]
            + candidate["points_per_play_weight"] * points_margin
            + candidate["elo_weight"] * elo_margin
        )
        market_margin = record.get("market_home_margin")
        if isinstance(market_margin, (int, float)):
            market_weight = parameters["market_anchor_weight"]
            baseline_prediction = (
                (1 - market_weight) * baseline_independent_prediction
                + market_weight * float(market_margin)
            )
            candidate_prediction = (
                (1 - market_weight) * candidate_independent_prediction
                + market_weight * float(market_margin)
            )
        else:
            baseline_prediction = baseline_independent_prediction
            candidate_prediction = candidate_independent_prediction
        baseline_errors.append(abs(target - baseline_prediction))
        calibrated_errors.append(abs(target - candidate_prediction))

    if not calibrated_errors:
        return parameters, {
            "status": "warming_up",
            "observations": count,
            "minimum_observations": MIN_CALIBRATION_GAMES,
        }

    baseline_mae = sum(baseline_errors) / len(baseline_errors)
    calibrated_mae = sum(calibrated_errors) / len(calibrated_errors)
    status = "unchanged"
    if calibrated_mae + 0.25 < baseline_mae:
        parameters = fit_model_parameters(completed, parameters)
        status = "updated"
    return parameters, {
        "status": status,
        "observations": count,
        "validation_games": len(calibrated_errors),
        "baseline_validation_mae": round(baseline_mae, 3),
        "calibrated_validation_mae": round(calibrated_mae, 3),
        "minimum_observations": MIN_CALIBRATION_GAMES,
    }
