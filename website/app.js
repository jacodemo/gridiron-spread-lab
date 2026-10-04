const gameList = document.querySelector("#game-list");
const searchInput = document.querySelector("#team-search");
const sortSelect = document.querySelector("#sort-select");
const emptyState = document.querySelector("#empty-state");
const filters = [...document.querySelectorAll(".filter-button")];
let games = [];
let modelParameters = { market_anchor_weight: 0.70 };
let activeFilter = "all";

const formatNumber = (value) => Number(value).toFixed(1);
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
})[character]);

function formatWeek(start, end) {
  const startDate = new Date(`${start}T12:00:00`);
  const endDate = new Date(`${end}T12:00:00`);
  const month = new Intl.DateTimeFormat("en-US", { month: "long" });
  const startMonth = month.format(startDate);
  const endMonth = month.format(endDate);
  if (startMonth === endMonth) {
    return `${startMonth} ${startDate.getDate()}–${endDate.getDate()}, ${endDate.getFullYear()}`;
  }
  return `${startMonth} ${startDate.getDate()} – ${endMonth} ${endDate.getDate()}, ${endDate.getFullYear()}`;
}

function formatKickoff(value) {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatUpdated(value) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function signed(value) {
  return `${value > 0 ? "+" : ""}${formatNumber(value)}`;
}

function spreadLabel(homeTeam, awayTeam, homeSpread, sportsbook = null) {
  if (homeSpread === null || homeSpread === undefined) {
    return '<span class="market-unavailable">Line unavailable</span>';
  }
  if (homeSpread === 0) {
    return `<span class="market-team">Pick’em</span>${sportsbook ? `<span class="market-book">${escapeHtml(sportsbook)}</span>` : ""}`;
  }
  const favorite = escapeHtml(homeSpread < 0 ? homeTeam : awayTeam);
  const spread = -Math.abs(homeSpread);
  return `<span class="market-team">${favorite}<b>${formatNumber(spread)}</b></span>${sportsbook ? `<span class="market-book">${escapeHtml(sportsbook)}</span>` : ""}`;
}

function differenceClass(difference) {
  if (difference >= 3) return "difference-home";
  if (difference <= -3) return "difference-away";
  return "difference-neutral";
}

function renderGame(game) {
  const homeTeam = escapeHtml(game.home_team);
  const awayTeam = escapeHtml(game.away_team);
  const hasProjection = Number.isFinite(game.predicted_home_spread);
  const hasMarketLine = Number.isFinite(game.market_home_spread);
  const difference = !hasProjection || !hasMarketLine
    ? null
    : game.market_home_spread - game.predicted_home_spread;
  const differenceLabel = difference === null
    ? `<span class="difference-value difference-missing">—</span><span class="difference-hint">${hasMarketLine ? "Model unavailable" : "Line pending"}</span>`
    : `<span class="difference-value ${differenceClass(difference)}">${signed(difference)}</span><span class="difference-hint">pts vs ${escapeHtml(game.sportsbook || "market")}</span>`;
  const lineCell = (provider) => {
    const line = game.market_lines?.[provider];
    return `<div class="spread-cell market-cell provider-${provider.toLowerCase()}">
      <span class="provider-name">${provider === "ESPN" ? "ESPN feed" : escapeHtml(provider)}</span>
      ${line === undefined ? '<span class="market-unavailable">—</span>' : spreadLabel(game.home_team, game.away_team, line)}
    </div>`;
  };
  const recommendation = game.recommendation
    ? `<div class="recommended-bet"><span>RECOMMENDED BET</span><strong>${escapeHtml(game.recommendation.team)} ${game.recommendation.spread === 0 ? "Pick’em" : signed(game.recommendation.spread)}</strong><small>${escapeHtml(game.recommendation.sportsbook || "Sportsbook")} · ${signed(game.recommendation.difference)}-point model gap</small></div>`
    : "";
  return `
    <article class="game-card">
      <div class="game-primary">
        <div class="matchup">
          <span class="kickoff">${formatKickoff(game.start_time)}</span>
          <strong class="away-team">${awayTeam}<span class="at-label">at</span></strong>
          <strong class="home-team">${homeTeam}<span class="home-indicator">HOME</span></strong>
        </div>
        <div class="spread-cell predicted-spread">
          ${hasProjection ? spreadLabel(game.home_team, game.away_team, game.predicted_home_spread) : '<span class="market-unavailable">Model unavailable</span>'}
        </div>
        ${lineCell("FanDuel")}
        ${lineCell("DraftKings")}
        ${lineCell("ESPN")}
        ${lineCell("SportsLine")}
        ${lineCell("VegasInsider")}
        <div class="difference-cell">${differenceLabel}</div>
      </div>
      ${recommendation}
      ${hasProjection ? `<details class="game-details">
        <summary>How we got the predicted spread <span aria-hidden="true">＋</span></summary>
        ${Number.isFinite(game.independent_model_home_spread) && hasMarketLine ? `<p class="prediction-note">Independent projection: ${spreadLabel(game.home_team, game.away_team, game.independent_model_home_spread)} · forecast is anchored ${formatNumber(modelParameters.market_anchor_weight * 100)}% to the selected market line.</p>` : ""}
        <div class="team-math">
          <div><strong>${awayTeam}</strong><span>Offense ${formatNumber(game.away_stats.offensive_points_per_play)} pts/play × ${formatNumber(game.away_stats.offensive_plays_per_game)} plays</span><span>Opponent rate ${formatNumber(game.home_stats.opponent_points_per_play)} pts/play × ${formatNumber(game.home_stats.opponent_plays_per_game)} plays</span><span>Elo rating ${formatNumber(game.away_elo)} vs ${formatNumber(game.home_elo)}</span></div>
          <div><strong>${homeTeam}</strong><span>Offense ${formatNumber(game.home_stats.offensive_points_per_play)} pts/play × ${formatNumber(game.home_stats.offensive_plays_per_game)} plays</span><span>Opponent rate ${formatNumber(game.away_stats.opponent_points_per_play)} pts/play × ${formatNumber(game.away_stats.opponent_plays_per_game)} plays</span><span>Elo rating ${formatNumber(game.home_elo)} vs ${formatNumber(game.away_elo)}</span></div>
        </div>
      </details>` : `<p class="prediction-note">Model projection unavailable: TeamRankings stats missing for ${escapeHtml((game.prediction_unavailable || []).join(", "))}.</p>`}
    </article>`;
}

function visibleGames() {
  const query = searchInput.value.trim().toLocaleLowerCase();
  let result = games.filter((game) => {
    const matchesSearch = !query || `${game.home_team} ${game.away_team}`.toLocaleLowerCase().includes(query);
    const hasLine = Number.isFinite(game.market_home_spread);
    const hasProjection = Number.isFinite(game.predicted_home_spread);
    const difference = hasLine && hasProjection
      ? game.market_home_spread - game.predicted_home_spread
      : null;
    const matchesFilter = activeFilter === "all"
      || (activeFilter === "picks" && Boolean(game.recommendation));
    return matchesSearch && matchesFilter;
  });

  if (sortSelect.value === "edge") {
    result.sort((a, b) => {
      const aDifference = Number.isFinite(a.market_home_spread) && Number.isFinite(a.predicted_home_spread)
        ? Math.abs(a.market_home_spread - a.predicted_home_spread)
        : -Infinity;
      const bDifference = Number.isFinite(b.market_home_spread) && Number.isFinite(b.predicted_home_spread)
        ? Math.abs(b.market_home_spread - b.predicted_home_spread)
        : -Infinity;
      return bDifference - aDifference;
    });
  } else if (sortSelect.value === "home") {
    result.sort((a, b) => a.home_team.localeCompare(b.home_team));
  } else {
    result.sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
  }
  return result;
}

function renderGames() {
  const shown = visibleGames();
  gameList.innerHTML = shown.length
    ? shown.map(renderGame).join("")
    : "";
  emptyState.hidden = shown.length !== 0;
  gameList.setAttribute("aria-busy", "false");
}

function updateSummary(data) {
  const lined = games.filter((game) => Number.isFinite(game.market_home_spread));
  const comparable = lined.filter((game) => Number.isFinite(game.predicted_home_spread));
  const lineCount = games.reduce(
    (total, game) => total + Object.keys(game.market_lines || {}).length,
    0,
  );
  const biggest = comparable.reduce((best, game) => {
    const difference = game.market_home_spread - game.predicted_home_spread;
    return Math.abs(difference) > Math.abs(best.difference) ? { difference, game } : best;
  }, { difference: 0, game: null });
  document.querySelector("#week-label").textContent = formatWeek(data.week_start, data.week_end);
  document.querySelector("#season-label").textContent = `${data.season} SEASON`;
  document.querySelector("#footer-season").textContent = `${data.season} FBS · DATA-DRIVEN, NOT GUARANTEED`;
  document.querySelector("#game-count").textContent = String(games.length).padStart(2, "0");
  document.querySelector("#line-count").textContent = String(lineCount);
  document.querySelector("#all-count").textContent = games.length;
  document.querySelector("#edge-count").textContent = games.filter(
    (game) => Boolean(game.recommendation),
  ).length;
  document.querySelector("#updated-label").textContent = formatUpdated(data.generated_at);
  modelParameters = {
    market_anchor_weight: 0.70,
    ...(data.model_parameters || {}),
  };
  const parameters = modelParameters;
  document.querySelector("#formula-description").textContent =
    `With a market line: ${formatNumber(parameters.market_anchor_weight * 100)}% market + ${formatNumber((1 - parameters.market_anchor_weight) * 100)}% independent projection. Without a line: intercept ${signed(parameters.intercept)} + (points-per-play margin × ${formatNumber(parameters.points_per_play_weight)}) + (Elo margin × ${formatNumber(parameters.elo_weight)}).`;
  if (biggest.game) {
    document.querySelector("#biggest-edge").textContent = `${biggest.difference >= 0 ? "HOME" : "AWAY"} ${formatNumber(Math.abs(biggest.difference))} pts`;
    document.querySelector("#biggest-edge-teams").textContent = `${biggest.game.away_team} at ${biggest.game.home_team}`;
  }
  if (data.skipped.length) {
    const notice = document.querySelector("#skipped-notice");
    const missing = data.skipped.map((game) => `${game.away_team} at ${game.home_team} (missing ${game.missing_stats.join(", ")})`);
    notice.textContent = `Model projections unavailable for ${missing.join("; ")} because TeamRankings stats were missing. Scheduled games remain listed.`;
    notice.hidden = false;
  }
  renderSeasonRecord(data.season_record);
  renderModelCalibration(data.season_record.model_calibration, parameters);
}

function renderModelCalibration(calibration, parameters) {
  const summary = document.querySelector("#calibration-summary");
  if (!calibration) {
    summary.textContent =
      `Calibration warming up: no saved forecast history yet. Current weights are ${formatNumber(parameters.points_per_play_weight)} points-per-play and ${formatNumber(parameters.elo_weight)} Elo.`;
    return;
  }
  if (calibration.status === "warming_up") {
    summary.textContent =
      `Calibration warming up: ${calibration.observations} / ${calibration.minimum_observations} completed forecast results. Current weights are ${formatNumber(parameters.points_per_play_weight)} points-per-play and ${formatNumber(parameters.elo_weight)} Elo.`;
    return;
  }
  const validation =
    `Walk-forward validation: ${calibration.calibrated_validation_mae} pts calibrated vs ${calibration.baseline_validation_mae} pts current over ${calibration.validation_games} games.`;
  const decision = calibration.status === "updated"
    ? `Weekly review updated the model using ${calibration.observations} completed forecasts.`
    : `Weekly review retained current weights; the candidate missed the 0.25-point improvement threshold.`;
  const weekly = calibration.weekly_review
    ? ` Last slate (${calibration.weekly_review.week_start}) MAE: ${calibration.weekly_review.mae} pts across ${calibration.weekly_review.completed_games} games.`
    : "";
  summary.textContent = `${decision} ${validation}${weekly}`;
}

function renderSeasonRecord(record) {
  const topRecord = document.querySelector("#season-record-top");
  topRecord.textContent = `${record.wins}–${record.losses}`;
  topRecord.setAttribute(
    "aria-label",
    `${record.wins} wins and ${record.losses} losses`,
  );
  document.querySelector("#season-record-top-note").textContent =
    `${record.pushes} pushes · ${record.pending} pending`;
  document.querySelector("#record-summary").textContent =
    `W–L–P: ${record.wins}–${record.losses}–${record.pushes} · ${record.pending} pending`;
  const rows = record.recommendations.map((pick) => {
    const kickoff = formatKickoff(pick.start_time);
    const finalScore = pick.final_home_score === null
      ? ""
      : ` · ${pick.final_away_score}–${pick.final_home_score}`;
    return `<tr>
      <td>${escapeHtml(kickoff)}<br><span>${escapeHtml(pick.away_team)} at ${escapeHtml(pick.home_team)}</span></td>
      <td>${escapeHtml(pick.team)}</td>
      <td>${pick.spread === 0 ? "Pick’em" : signed(pick.spread)}</td>
      <td>${escapeHtml(pick.sportsbook || "—")}</td>
      <td><span class="result-${escapeHtml(pick.status)}">${escapeHtml(pick.status)}${escapeHtml(finalScore)}</span></td>
    </tr>`;
  });
  document.querySelector("#record-list").innerHTML = rows.length
    ? rows.join("")
    : '<tr><td colspan="5">No qualifying recommendations recorded yet.</td></tr>';
}

async function loadBoard() {
  try {
    const response = await fetch("./data.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Data request failed (${response.status})`);
    const data = await response.json();
    games = data.games;
    updateSummary(data);
    renderGames();
  } catch (error) {
    gameList.setAttribute("aria-busy", "false");
    gameList.innerHTML = `<div class="error-state"><strong>Couldn’t load this week’s board.</strong><span>${error.message}. Try refreshing after the next successful site build.</span></div>`;
  }
}

filters.forEach((button) => button.addEventListener("click", () => {
  activeFilter = button.dataset.filter;
  filters.forEach((filter) => {
    const isActive = filter === button;
    filter.classList.toggle("is-active", isActive);
    filter.setAttribute("aria-pressed", String(isActive));
  });
  renderGames();
}));
searchInput.addEventListener("input", renderGames);
sortSelect.addEventListener("change", renderGames);
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    searchInput.focus();
  }
  if (event.key === "Escape" && document.activeElement === searchInput) {
    searchInput.value = "";
    renderGames();
    searchInput.blur();
  }
});

loadBoard();
