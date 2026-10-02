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

function marketLabel(game) {
  if (game.market_home_margin === null || game.market_home_margin === undefined) {
    return '<span class="market-unavailable">Line unavailable</span>';
  }
  const favorite = escapeHtml(game.market_home_margin > 0 ? game.home_team : game.away_team);
  const spread = -Math.abs(game.market_home_margin);
  if (game.market_home_margin === 0) {
    return `<span class="market-team">Pick’em</span><span class="market-book">${escapeHtml(game.sportsbook)}</span>`;
  }
  return `<span class="market-team">${favorite}<b>${formatNumber(spread)}</b></span><span class="market-book">${escapeHtml(game.sportsbook)}</span>`;
}

function edgeClass(edge) {
  if (edge >= 3) return "edge-home";
  if (edge <= -3) return "edge-away";
  return "edge-neutral";
}

function renderGame(game) {
  const homeTeam = escapeHtml(game.home_team);
  const awayTeam = escapeHtml(game.away_team);
  const edge = game.market_home_margin === null || game.market_home_margin === undefined
    ? null
    : game.projected_home_margin - game.market_home_margin;
  const edgeLabel = edge === null
    ? '<span class="edge-value edge-missing">—</span><span class="edge-hint">No market line</span>'
    : `<span class="edge-value ${edgeClass(edge)}">${signed(edge)}</span><span class="edge-hint">${edge >= 0 ? "model leans home" : "model leans away"}</span>`;
  return `
    <article class="game-card">
      <div class="game-primary">
        <div class="matchup">
          <span class="kickoff">${formatKickoff(game.start_time)}</span>
          <strong class="away-team">${awayTeam}<span class="at-label">at</span></strong>
          <strong class="home-team">${homeTeam}<span class="home-indicator">HOME</span></strong>
        </div>
        <div class="projected-score">
          <span class="projection-label">PROJECTED</span>
          <strong><span>${formatNumber(game.away_points)}</span><i>—</i><span>${formatNumber(game.home_points)}</span></strong>
          <small>${awayTeam} <i>·</i> ${homeTeam}</small>
        </div>
        <div class="market-cell">${marketLabel(game)}</div>
        <div class="edge-cell">${edgeLabel}</div>
      </div>
      <details class="game-details">
        <summary>How we got the score <span aria-hidden="true">＋</span></summary>
        <div class="team-math">
          <div><strong>${awayTeam}</strong><span>Offense ${formatNumber(game.away_stats.offensive_points_per_play)} pts/play × ${formatNumber(game.away_stats.offensive_plays_per_game)} plays</span><span>Opponent rate ${formatNumber(game.home_stats.opponent_points_per_play)} pts/play × ${formatNumber(game.home_stats.opponent_plays_per_game)} plays</span><b>Projected ${formatNumber(game.away_points)} pts</b></div>
          <div><strong>${homeTeam}</strong><span>Offense ${formatNumber(game.home_stats.offensive_points_per_play)} pts/play × ${formatNumber(game.home_stats.offensive_plays_per_game)} plays</span><span>Opponent rate ${formatNumber(game.away_stats.opponent_points_per_play)} pts/play × ${formatNumber(game.away_stats.opponent_plays_per_game)} plays</span><b>Projected ${formatNumber(game.home_points)} pts</b></div>
        </div>
      </details>
    </article>`;
}

function visibleGames() {
  const query = searchInput.value.trim().toLocaleLowerCase();
  let result = games.filter((game) => {
    const matchesSearch = !query || `${game.home_team} ${game.away_team}`.toLocaleLowerCase().includes(query);
    const hasLine = game.market_home_margin !== null && game.market_home_margin !== undefined;
    const edge = hasLine ? game.projected_home_margin - game.market_home_margin : null;
    const matchesFilter = activeFilter === "all"
      || (activeFilter === "market" && hasLine)
      || (activeFilter === "edge" && edge !== null && Math.abs(edge) >= 3);
    return matchesSearch && matchesFilter;
  });

  if (sortSelect.value === "edge") {
    result.sort((a, b) => {
      const aEdge = a.market_home_margin === null ? -Infinity : Math.abs(a.projected_home_margin - a.market_home_margin);
      const bEdge = b.market_home_margin === null ? -Infinity : Math.abs(b.projected_home_margin - b.market_home_margin);
      return bEdge - aEdge;
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
  const lined = games.filter((game) => game.market_home_margin !== null && game.market_home_margin !== undefined);
  const biggest = lined.reduce((best, game) => {
    const edge = game.projected_home_margin - game.market_home_margin;
    return Math.abs(edge) > Math.abs(best.edge) ? { edge, game } : best;
  }, { edge: 0, game: null });
  document.querySelector("#week-label").textContent = formatWeek(data.week_start, data.week_end);
  document.querySelector("#season-label").textContent = `${data.season} SEASON`;
  document.querySelector("#footer-season").textContent = `${data.season} FBS · DATA-DRIVEN, NOT GUARANTEED`;
  document.querySelector("#game-count").textContent = String(games.length).padStart(2, "0");
  document.querySelector("#line-count").textContent = `${lined.length} / ${games.length}`;
  document.querySelector("#all-count").textContent = games.length;
  document.querySelector("#market-count").textContent = lined.length;
  document.querySelector("#edge-count").textContent = games.filter((game) => (
    game.market_home_margin !== null
    && game.market_home_margin !== undefined
    && Math.abs(game.projected_home_margin - game.market_home_margin) >= 3
  )).length;
  document.querySelector("#updated-label").textContent = formatUpdated(data.generated_at);
  if (biggest.game) {
    document.querySelector("#biggest-edge").textContent = `${biggest.edge >= 0 ? "HOME" : "AWAY"} ${formatNumber(Math.abs(biggest.edge))}`;
    document.querySelector("#biggest-edge-teams").textContent = `${biggest.game.away_team} at ${biggest.game.home_team}`;
  }
  if (data.skipped.length) {
    const notice = document.querySelector("#skipped-notice");
    const missing = data.skipped.map((game) => `${game.away_team} at ${game.home_team} (missing ${game.missing_stats.join(", ")})`);
    notice.textContent = `${data.skipped.length} matchup${data.skipped.length === 1 ? "" : "s"} omitted: ${missing.join("; ")}. TeamRankings stats were not available for every team.`;
    notice.hidden = false;
  }
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
