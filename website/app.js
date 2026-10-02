const gameList = document.querySelector("#game-list");
const searchInput = document.querySelector("#team-search");
const sortSelect = document.querySelector("#sort-select");
const emptyState = document.querySelector("#empty-state");
const filters = [...document.querySelectorAll(".filter-button")];
let games = [];
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
  const difference = game.market_home_spread === null || game.market_home_spread === undefined
    ? null
    : game.market_home_spread - game.predicted_home_spread;
  const differenceLabel = difference === null
    ? '<span class="difference-value difference-missing">—</span><span class="difference-hint">No sportsbook line</span>'
    : `<span class="difference-value ${differenceClass(difference)}">${signed(difference)}</span><span class="difference-hint">pts toward ${difference >= 0 ? "home" : "away"}</span>`;
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
          ${spreadLabel(game.home_team, game.away_team, game.predicted_home_spread)}
        </div>
        <div class="spread-cell market-cell">
          ${spreadLabel(game.home_team, game.away_team, game.market_home_spread, game.sportsbook)}
        </div>
        <div class="difference-cell">${differenceLabel}</div>
      </div>
      ${recommendation}
      <details class="game-details">
        <summary>How we got the predicted spread <span aria-hidden="true">＋</span></summary>
        <div class="team-math">
          <div><strong>${awayTeam}</strong><span>Offense ${formatNumber(game.away_stats.offensive_points_per_play)} pts/play × ${formatNumber(game.away_stats.offensive_plays_per_game)} plays</span><span>Opponent rate ${formatNumber(game.home_stats.opponent_points_per_play)} pts/play × ${formatNumber(game.home_stats.opponent_plays_per_game)} plays</span><span>Elo rating ${formatNumber(game.away_elo)} vs ${formatNumber(game.home_elo)}</span></div>
          <div><strong>${homeTeam}</strong><span>Offense ${formatNumber(game.home_stats.offensive_points_per_play)} pts/play × ${formatNumber(game.home_stats.offensive_plays_per_game)} plays</span><span>Opponent rate ${formatNumber(game.away_stats.opponent_points_per_play)} pts/play × ${formatNumber(game.away_stats.opponent_plays_per_game)} plays</span><span>Elo rating ${formatNumber(game.home_elo)} vs ${formatNumber(game.away_elo)}</span></div>
        </div>
      </details>
    </article>`;
}

function visibleGames() {
  const query = searchInput.value.trim().toLocaleLowerCase();
  let result = games.filter((game) => {
    const matchesSearch = !query || `${game.home_team} ${game.away_team}`.toLocaleLowerCase().includes(query);
    const hasLine = game.market_home_spread !== null && game.market_home_spread !== undefined;
    const difference = hasLine ? game.market_home_spread - game.predicted_home_spread : null;
    const matchesFilter = activeFilter === "all"
      || (activeFilter === "market" && hasLine)
      || (activeFilter === "edge" && difference !== null && Math.abs(difference) >= 3);
    return matchesSearch && matchesFilter;
  });

  if (sortSelect.value === "edge") {
    result.sort((a, b) => {
      const aDifference = a.market_home_spread === null ? -Infinity : Math.abs(a.market_home_spread - a.predicted_home_spread);
      const bDifference = b.market_home_spread === null ? -Infinity : Math.abs(b.market_home_spread - b.predicted_home_spread);
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
  const lined = games.filter((game) => game.market_home_spread !== null && game.market_home_spread !== undefined);
  const biggest = lined.reduce((best, game) => {
    const difference = game.market_home_spread - game.predicted_home_spread;
    return Math.abs(difference) > Math.abs(best.difference) ? { difference, game } : best;
  }, { difference: 0, game: null });
  document.querySelector("#week-label").textContent = formatWeek(data.week_start, data.week_end);
  document.querySelector("#season-label").textContent = `${data.season} SEASON`;
  document.querySelector("#footer-season").textContent = `${data.season} FBS · DATA-DRIVEN, NOT GUARANTEED`;
  document.querySelector("#game-count").textContent = String(games.length).padStart(2, "0");
  document.querySelector("#line-count").textContent = `${lined.length} / ${games.length}`;
  document.querySelector("#all-count").textContent = games.length;
  document.querySelector("#market-count").textContent = lined.length;
  document.querySelector("#edge-count").textContent = games.filter((game) => (
    game.market_home_spread !== null
    && game.market_home_spread !== undefined
    && Math.abs(game.market_home_spread - game.predicted_home_spread) >= 3
  )).length;
  document.querySelector("#updated-label").textContent = formatUpdated(data.generated_at);
  if (biggest.game) {
    document.querySelector("#biggest-edge").textContent = `${biggest.difference >= 0 ? "HOME" : "AWAY"} ${formatNumber(Math.abs(biggest.difference))} pts`;
    document.querySelector("#biggest-edge-teams").textContent = `${biggest.game.away_team} at ${biggest.game.home_team}`;
  }
  if (data.skipped.length) {
    const notice = document.querySelector("#skipped-notice");
    const missing = data.skipped.map((game) => `${game.away_team} at ${game.home_team} (missing ${game.missing_stats.join(", ")})`);
    notice.textContent = `${data.skipped.length} matchup${data.skipped.length === 1 ? "" : "s"} omitted: ${missing.join("; ")}. TeamRankings stats were not available for every team.`;
    notice.hidden = false;
  }
  renderSeasonRecord(data.season_record);
}

function renderSeasonRecord(record) {
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
