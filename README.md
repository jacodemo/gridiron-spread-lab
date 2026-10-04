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
6. Form an independent home-margin projection using the points-per-play, Elo, and intercept weights. When a sportsbook line is available, blend that projection with the selected market-implied home margin: currently, 70% market and 30% independent projection. Without a market line, use the independent projection alone. The displayed home spread is the negative of the predicted home margin. The independent projection remains visible in each game's details so the market adjustment is transparent.

The market anchor is a conservative starting prior, not a claim that the market is infallible or that 70% is a learned optimum. Research on college-football betting finds that market lines contain useful score-prediction information while also documenting forms of market inefficiency ([Arscott, 2023](https://doi.org/10.1177/15270025221148991); [Kuester & Sanders, 2011](https://doi.org/10.1007/s12197-009-9113-3)). This project's prior forecast history is empty, so an empirically estimated blend is not yet supportable. The 70% anchor is fixed for now; weekly regression adjusts the independent projection's weights only when chronological validation clears the improvement threshold.

The board posts 10–15 picks when at least 10 scheduled games have both a model projection and a market line. It always selects the 10 largest absolute model-to-market gaps, then adds up to five more picks when their gap is at least 3 points; if more than 15 games meet that threshold, only the 15 largest gaps are selected. This makes the weekly pick count explicit rather than labeling every qualifying discrepancy a bet. Picks are created only after the prior recommendation week is complete, then locked for that Monday–Sunday slate. Wednesday and Friday refreshes may update projections and lines, but do not change that week's picks or saved wager lines. Completed picks remain in the historical record.

After Sunday results are graded, the season updater archives completed scores and the pregame market line for every saved projection, not just recommended bets. Once at least 40 forecast outcomes have accumulated, it fits a regularized linear regression over the points-per-play margin, Elo margin, and intercept. Market-lined games are de-anchored using the current blend before fitting, and chronological walk-forward validation evaluates both the candidate and existing parameters using the same market blend that generates displayed forecasts. Proposed weights are adopted only when validation improves mean absolute score-margin error by at least 0.25 points. Otherwise, existing weights are retained. Older results without saved pregame inputs are not reconstructed or used as training data.

The model uses these TeamRankings season columns:

- `points-per-play`
- `plays-per-game`
- `opponent-plays-per-game`
- `opponent-points-per-play`

The two FBS teams named Miami are labeled **Miami (FL)** and **Miami (OH)** on the board and matched independently in schedule and statistics data.

The current week's FBS schedule, game results, and ESPN-feed lines come from ESPN. The weekly scoreboard is supplemented from active TeamRankings teams' ESPN schedules so upcoming games missing from the scoreboard still appear. The board displays FanDuel and DraftKings lines from ESPN and SportsLine data, the ESPN feed's listed line, SportsLine consensus, and VegasInsider consensus independently whenever available. VegasInsider is queried when a matchup has no line from ESPN or SportsLine, and its consensus line fills that matchup's VegasInsider column and selected market line. Completed VegasInsider rows that no longer expose a kickoff date are matched by both teams. Scheduled games remain visible when lines are pending, and games missing TeamRankings stats are shown without a model projection or recommendation. The recommendation and tracked result use one available line in this order: FanDuel, DraftKings, ESPN feed, SportsLine consensus, then VegasInsider consensus. The difference is the selected market's home spread minus the predicted home spread: a positive value favors the home side, while a negative value favors the away side. Picks are selected and ranked from scheduled games with projections and sportsbook lines as described above. The season record saves each selected pick before kickoff and grades it against the saved line as a win, loss, or push. Picks are not replaced during the week; recommendations for the next slate wait until every pick in the latest recommendation week has been graded. It is an informational model record, not a guarantee or betting advice.

All inputs are live external data. A missing or changed source table raises an error instead of silently substituting sample values.

## Website features

- Product-style responsive dashboard with matchup, model-method, and season-record navigation.
- Search scheduled games and filter to the 10–15 ranked weekly picks.
- Sort by kickoff, home team, or absolute spread difference.
- See qualifying recommended bets and expand each game to inspect the TeamRankings averages and Elo ratings behind its predicted spread.
- Compare FanDuel, DraftKings, the ESPN feed line, SportsLine consensus, and fallback VegasInsider consensus independently when available.
- Follow the season's recommended-bet W–L–P record and final results.
- See the active season W–L record in the top summary bar.
- View mobile-friendly matchup cards and a plain-language explanation of the model.
