# Gridiron Spread Lab

Weekly FBS score and spread projections using current season team averages from TeamRankings.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
python -m pytest
python -m gridiron_spread.cli
```

Pass any date in the target week to select a different Monday-through-Sunday window:

```powershell
python -m gridiron_spread.cli --date 2026-10-03
```

## Projection method

For each team:

1. Offensive output = points per play × offensive plays per game.
2. Opponent output = opponent points per play × opponent plays per game.
3. Projected points = the average of that team's offensive output and its opponent's opponent output.
4. Projected home margin = projected home points − projected away points.

The model uses these TeamRankings season columns:

- `points-per-play`
- `plays-per-game`
- `opponent-plays-per-game`
- `opponent-points-per-play`

The current week's FBS schedule comes from ESPN's public scoreboard feed. Market lines prefer FanDuel and fall back to DraftKings when the feed does not provide a FanDuel line. Each line is labeled with its sportsbook; unavailable lines are reported explicitly. The `Edge` column is the model's projected home margin minus the market's implied home margin.

All inputs are live external data. A missing or changed source table raises an error instead of silently substituting sample values.
