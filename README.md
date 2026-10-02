# Gridiron Spread Lab

An open, readable weekly FBS predicted-spread dashboard with an Elo-adjusted model, multiple sportsbook-line sources, recommended spread picks, and a season results ledger.

**Live site:** [cfb-spread-predictions.dev](https://cfb-spread-predictions.dev/) · [Cloudflare Pages fallback](https://gridiron-spread-lab.pages.dev/)

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
4. The custom domain `cfb-spread-predictions.dev` is attached to the Pages project. Cloudflare can configure DNS automatically when the domain is active in the same account. For an apex/root domain, its nameservers must point to Cloudflare.

The project is connected to GitHub; pushes to `main` trigger a Pages rebuild. GitHub Actions refreshes spreads every Wednesday and Friday at 8:00 a.m. America/Chicago and grades completed recommendations every Sunday at 11:00 p.m. America/Chicago. Scheduled workflows store Elo ratings and recommendation results in `data/season_record.json`; their commits trigger Cloudflare Pages deployments. The workflow also supports manual refresh and grading runs from the GitHub Actions tab.

Cloudflare's custom-domain flow and apex-domain requirements are documented in [Cloudflare Pages custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/). The Pages build command and output directory are configured as described in [Cloudflare Pages build configuration](https://developers.cloudflare.com/pages/configuration/build-configuration/).

## Projection method

For each team:

1. Offensive output = points per play × offensive plays per game.
2. Opponent output = opponent points per play × opponent plays per game.
3. Estimate each team's scoring pace as the average of its offensive output and its opponent's opponent output.
4. Calculate the points-per-play home margin from both scoring estimates.
5. Calculate the Elo home margin as `(home Elo − away Elo) / 25`. Teams begin each season at 1500 Elo; completed FBS results update ratings using the standard 400-point expected-score curve and a K-factor of 20.
6. Blend the points-per-play and Elo home margins equally. The displayed home spread is the negative of that blended home margin.

The model uses these TeamRankings season columns:

- `points-per-play`
- `plays-per-game`
- `opponent-plays-per-game`
- `opponent-points-per-play`

The current week's FBS schedule, game results, and ESPN-feed lines come from ESPN's public scoreboard feed. The board displays FanDuel and DraftKings lines from ESPN and SportsLine data, the ESPN feed's listed line, and SportsLine consensus independently whenever each is available. Matchups without a spread from any source are omitted from the board; missing lines are still shown per source for the games that do have a line. The recommendation and tracked result use one available line in this order: FanDuel, DraftKings, ESPN feed, then SportsLine consensus. The difference is the selected market's home spread minus the predicted home spread: a positive value favors the home side, while a negative value favors the away side. A recommendation is shown when the difference is at least three points in absolute value and a sportsbook spread is available. The season record saves one latest qualifying recommendation per game before kickoff and grades it against the saved line as a win, loss, or push. It is an informational model record, not a guarantee or betting advice.

All inputs are live external data. A missing or changed source table raises an error instead of silently substituting sample values.

## Website features

- Search teams and filter the listed-spread games to differences of at least three points.
- Sort by kickoff, home team, or absolute spread difference.
- See qualifying recommended bets and expand each game to inspect the TeamRankings averages and Elo ratings behind its predicted spread.
- Compare FanDuel, DraftKings, the ESPN feed line, and SportsLine consensus independently when available.
- Follow the season's recommended-bet W–L–P record and final results.
- View mobile-friendly matchup cards and a plain-language explanation of the model.
