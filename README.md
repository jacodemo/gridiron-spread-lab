# Gridiron Spread Lab

An open, readable weekly FBS score and spread dashboard. Team projections use current-season averages from TeamRankings; matchup schedules and sportsbook lines come from ESPN.

## Run the command-line model

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

## Build the website

The static website is generated from the same Python model and current source data:

```powershell
python scripts/build_site.py
python -m http.server 8000 --directory dist
```

Open `http://localhost:8000` to view it. A new build fetches the latest schedule, TeamRankings season stats, and available market lines. If an upstream source fails or changes format, the build fails instead of publishing made-up or silently stale forecasts.

## Deploy to Cloudflare Pages

The site is prepared for the registered domain `cfb-spread-predictions.dev`.

1. In the Cloudflare dashboard, open **Workers & Pages** and create a Pages project by connecting the `jacodemo/gridiron-spread-lab` GitHub repository.
2. Use the following build settings:
   - Production branch: `main`
   - Build command: `python scripts/build_site.py`
   - Build output directory: `dist`
   - Root directory: `/`
3. Deploy the project. The published board reflects data fetched at build time; trigger a new deployment to refresh it.
4. In the Pages project, select **Custom domains** → **Set up a domain** and enter `cfb-spread-predictions.dev`. Cloudflare can configure DNS automatically when the domain is active in the same account. For an apex/root domain, its nameservers must point to Cloudflare.

Cloudflare's custom-domain flow and apex-domain requirements are documented in [Cloudflare Pages custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/). The Pages build command and output directory are configured as described in [Cloudflare Pages build configuration](https://developers.cloudflare.com/pages/configuration/build-configuration/).

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

## Website features

- Search teams and filter to games with a market line or a model edge of at least three points.
- Sort by kickoff, home team, or absolute model edge.
- Expand each game to see the team averages that produced its projected score.
- See the sportsbook for each line; FanDuel is preferred, with DraftKings as the fallback.
- View mobile-friendly matchup cards and a plain-language explanation of the model.
