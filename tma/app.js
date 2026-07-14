const tg = window.Telegram?.WebApp;
const state = {
  token: "",
  user: null,
  view: "grid",
  difficulty: "medium",
  category: "all",
  leaderboardScope: "global",
  gameOptions: null,
  gridQuestion: null,
  timerId: null,
  timerTotal: 15,
  timerDeadline: 0,
  matchPollId: null,
  duelSocket: null,
  duelId: null,
  duelAnswered: false,
  duelSelectedButton: null,
};

const els = {
  shell: document.getElementById("app-shell"),
  authPanel: document.getElementById("auth-panel"),
  screenTitle: document.getElementById("screen-title"),
  scoreValue: document.getElementById("score-value"),
  tabs: document.querySelectorAll(".tab"),
  views: document.querySelectorAll(".view"),
  difficultyControls: document.getElementById("difficulty-controls"),
  categorySelect: document.getElementById("category-select"),
  gridKicker: document.getElementById("grid-kicker"),
  gridPrompt: document.getElementById("grid-prompt"),
  promptFlag: document.getElementById("prompt-flag"),
  flagGrid: document.getElementById("flag-grid"),
  gridFeedback: document.getElementById("grid-feedback"),
  gridNext: document.getElementById("grid-next"),
  timerProgress: document.getElementById("timer-progress"),
  timerText: document.getElementById("timer-text"),
  multiplier: document.getElementById("multiplier"),
  dailyStart: document.getElementById("daily-start"),
  dailyTotal: document.getElementById("daily-total"),
  dailyLeaders: document.getElementById("daily-leaders"),
  dailyFeedback: document.getElementById("daily-feedback"),
  duelFormat: document.getElementById("duel-format"),
  quickMatch: document.getElementById("quick-match"),
  startDuel: document.getElementById("start-duel"),
  cancelMatch: document.getElementById("cancel-match"),
  duelStatus: document.getElementById("duel-status"),
  duelYou: document.getElementById("duel-you"),
  duelThem: document.getElementById("duel-them"),
  duelRound: document.getElementById("duel-round"),
  duelRoundLabel: document.getElementById("duel-round-label"),
  duelPrompt: document.getElementById("duel-prompt"),
  duelGrid: document.getElementById("duel-grid"),
  profileName: document.getElementById("profile-name"),
  profileAvatar: document.getElementById("profile-avatar"),
  totalAnswers: document.getElementById("total-answers"),
  accuracy: document.getElementById("accuracy"),
  avgTime: document.getElementById("avg-time"),
  bestStreak: document.getElementById("best-streak"),
  correctBar: document.getElementById("correct-bar"),
  missedBar: document.getElementById("missed-bar"),
  badges: document.getElementById("badges"),
  leaders: document.getElementById("leaders"),
  rankWindow: document.getElementById("rank-window"),
  leaderboardScope: document.getElementById("leaderboard-scope"),
  toastStack: document.getElementById("toast-stack"),
  confetti: document.getElementById("confetti-canvas"),
};

boot();

async function boot() {
  tg?.ready();
  tg?.expand();
  applyTelegramTheme();
  bindEvents();
  try {
    await authenticate();
    els.authPanel.hidden = true;
    state.gameOptions = await api("/api/game/options");
    renderGameControls();
    showView("grid");
    await loadGridQuestion();
  } catch (error) {
    renderError(els.authPanel, error.message || "Could not open the game session.");
  }
}

function applyTelegramTheme() {
  if (tg?.viewportHeight) {
    document.documentElement.style.setProperty("--tg-viewport-height", `${tg.viewportHeight}px`);
  }
  if (!tg?.themeParams) return;
  const root = document.documentElement;
  Object.entries(tg.themeParams).forEach(([key, value]) => {
    root.style.setProperty(`--tg-theme-${kebab(key)}`, value);
  });
}

function bindEvents() {
  els.tabs.forEach((tab) => {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  });
  els.gridNext.addEventListener("click", loadGridQuestion);
  els.dailyStart.addEventListener("click", startDailyChallenge);
  els.categorySelect.addEventListener("change", () => {
    state.category = els.categorySelect.value;
    loadGridQuestion();
  });
  els.quickMatch.addEventListener("click", joinQuickMatch);
  els.startDuel.addEventListener("click", startDuel);
  els.cancelMatch.addEventListener("click", cancelQuickMatch);
  els.leaderboardScope.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      state.leaderboardScope = button.dataset.scope;
      els.leaderboardScope.querySelectorAll("button").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      loadLeaderboard();
    });
  });
}

async function authenticate() {
  const initData = tg?.initData || "";
  const result = await api("/api/auth/telegram", {
    method: "POST",
    body: JSON.stringify({ init_data: initData }),
    skipAuth: true,
  });
  state.token = result.token;
  state.user = result.user;
}

function renderGameControls() {
  els.difficultyControls.innerHTML = "";
  state.gameOptions.difficulties.forEach((tier) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = tier.label;
    button.classList.toggle("active", tier.key === state.difficulty);
    button.addEventListener("click", () => {
      state.difficulty = tier.key;
      els.difficultyControls.querySelectorAll("button").forEach((node) => node.classList.toggle("active", node === button));
      loadGridQuestion();
    });
    els.difficultyControls.appendChild(button);
  });

  els.categorySelect.innerHTML = "";
  state.gameOptions.categories.forEach((pack) => {
    const option = document.createElement("option");
    option.value = pack.key;
    option.textContent = pack.label;
    els.categorySelect.appendChild(option);
  });
  els.categorySelect.value = state.category;
  els.dailyTotal.textContent = state.gameOptions.daily_total || 12;
}

async function showView(view) {
  state.view = view;
  els.screenTitle.textContent = titleFor(view);
  els.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  els.views.forEach((section) => {
    section.hidden = section.id !== `${view}-view`;
    section.classList.toggle("active", !section.hidden);
  });
  if (view !== "grid") stopTimer();
  if (view === "daily") await loadDaily();
  if (view === "profile") await loadProfile();
  if (view === "leaderboard") await loadLeaderboard();
}

async function loadGridQuestion() {
  stopTimer();
  clearFeedback(els.gridFeedback);
  els.flagGrid.innerHTML = skeletonChoices(6);
  els.promptFlag.hidden = true;
  try {
    const params = new URLSearchParams({
      mode: "grid",
      difficulty: state.difficulty,
      category: state.category,
    });
    const question = await api(`/api/quiz/question?${params}`);
    state.gridQuestion = question;
    state.timerTotal = question.timer_seconds || 15;
    renderPrompt(question);
    renderGridChoices(question);
    startTimer();
  } catch (error) {
    renderError(els.flagGrid, error.message || "Could not load a question.");
  }
}

function renderPrompt(question) {
  const prompt = question.prompt || {};
  const categoryLabel = labelForCategory(question.category);
  if (prompt.type === "flag_to_capital") {
    els.gridKicker.textContent = `${categoryLabel} / capital cities`;
    els.gridPrompt.textContent = prompt.text;
    els.promptFlag.innerHTML = `<img alt="Flag of ${escapeAttr(prompt.country)}" src="${escapeAttr(prompt.flag_url)}">`;
    els.promptFlag.hidden = false;
  } else {
    els.gridKicker.textContent = categoryLabel;
    els.gridPrompt.textContent = prompt.text || "Find the flag";
    els.promptFlag.hidden = true;
  }
  if (question.daily_progress) {
    const { answered, total } = question.daily_progress;
    els.gridKicker.textContent = `Daily challenge ${Math.min(answered + 1, total)}/${total}`;
  }
}

function renderGridChoices(question) {
  els.flagGrid.innerHTML = "";
  question.choices.forEach((choice, index) => {
    const button = document.createElement("button");
    button.className = "flag-choice";
    button.type = "button";
    button.dataset.choiceId = choice.id;
    button.style.setProperty("--i", index);
    if (choice.flag_url) {
      button.innerHTML = `<img alt="Flag option" src="${escapeAttr(choice.flag_url)}">`;
    } else {
      button.innerHTML = `<span class="choice-label">${escapeHtml(choice.label)}</span>`;
    }
    button.addEventListener("click", () => answerGrid(choice.id, button));
    els.flagGrid.appendChild(button);
  });
}

async function answerGrid(choiceId, button) {
  stopTimer();
  disableGrid(true);
  const idempotencyKey = randomKey();
  try {
    const result = await api("/api/quiz/answer", {
      method: "POST",
      body: JSON.stringify({
        question_id: state.gridQuestion.question_id,
        choice_id: choiceId,
        idempotency_key: idempotencyKey,
      }),
    });
    applyAnswerResult(result, button, els.gridFeedback);
    window.setTimeout(loadGridQuestion, result.suspicious ? 1600 : 1150);
  } catch (error) {
    feedback(els.gridFeedback, error.message || "Answer was not saved.", false);
    disableGrid(false);
  }
}

function applyAnswerResult(result, button, feedbackEl) {
  els.scoreValue.textContent = result.stats.score;
  els.multiplier.textContent = `x${Number(result.multiplier || 1).toFixed(1)}`;
  if (result.correct && !result.suspicious) {
    button?.classList.add("correct");
    feedback(feedbackEl, `Correct: ${result.correct_answer} +${result.points_awarded}`, true);
    successFeedback();
    burstConfetti();
  } else if (result.suspicious) {
    button?.classList.add("wrong");
    feedback(feedbackEl, "Too fast to count. Try again at human speed.", false);
    errorFeedback();
  } else {
    button?.classList.add("wrong");
    els.shell.classList.add("shake");
    window.setTimeout(() => els.shell.classList.remove("shake"), 240);
    feedback(feedbackEl, `Missed: ${result.correct_answer}`, false);
    errorFeedback();
  }
  (result.new_badges || []).forEach(showBadgeToast);
}

async function loadDaily() {
  els.dailyLeaders.innerHTML = skeletonRows(4);
  try {
    const data = await api("/api/leaderboard?scope=daily");
    renderLeaderList(els.dailyLeaders, data.leaders || [], false);
  } catch (error) {
    renderError(els.dailyLeaders, error.message || "Could not load daily leaderboard.");
  }
}

async function startDailyChallenge() {
  state.category = "daily";
  els.categorySelect.value = "daily";
  feedback(els.dailyFeedback, "Daily route loaded in Quiz.", true);
  await showView("grid");
  await loadGridQuestion();
}

async function joinQuickMatch() {
  feedback(els.duelStatus, "Searching for an opponent...", true);
  try {
    const result = await api("/api/matchmaking/quick-match", {
      method: "POST",
      body: JSON.stringify({ format: els.duelFormat.value }),
    });
    if (result.status === "ready") {
      feedback(els.duelStatus, `Duel ready: ${result.duel_id}`, true);
      connectDuel(result.duel_id);
    } else {
      feedback(els.duelStatus, "Waiting in queue. Keep this screen open.", true);
      startMatchPolling();
    }
  } catch (error) {
    feedback(els.duelStatus, error.message || "Could not join matchmaking.", false);
  }
}

async function cancelQuickMatch() {
  try {
    await api("/api/matchmaking/quick-match", { method: "DELETE" });
    stopMatchPolling();
    closeDuelSocket();
    els.startDuel.disabled = true;
    feedback(els.duelStatus, "Queue cancelled.", true);
  } catch (error) {
    feedback(els.duelStatus, error.message || "Could not cancel queue.", false);
  }
}

function startMatchPolling() {
  stopMatchPolling();
  state.matchPollId = window.setInterval(async () => {
    try {
      const result = await api("/api/matchmaking/status");
      if (result.duel_id) {
        stopMatchPolling();
        feedback(els.duelStatus, `Duel ready: ${result.duel_id}`, true);
        connectDuel(result.duel_id);
      }
    } catch (error) {
      stopMatchPolling();
      feedback(els.duelStatus, error.message || "Matchmaking status failed.", false);
    }
  }, 2000);
}

function stopMatchPolling() {
  if (state.matchPollId) window.clearInterval(state.matchPollId);
  state.matchPollId = null;
}

function connectDuel(duelId) {
  closeDuelSocket();
  state.duelId = duelId;
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/duel/${duelId}?token=${encodeURIComponent(state.token)}`);
  state.duelSocket = socket;
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "ready", at: Date.now() }));
  });
  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "presence") {
      els.startDuel.disabled = data.players < 2;
      feedback(els.duelStatus, `Duel room live: ${data.players} unique player${data.players === 1 ? "" : "s"} connected.`, true);
    }
    if (data.type === "duel_waiting") {
      feedback(els.duelStatus, data.message || "Waiting for another player.", false);
    }
    if (data.type === "duel_question") {
      renderDuelQuestion(data);
    }
    if (data.type === "duel_answer_result") {
      renderDuelScores(data.scores);
      if (Number(data.user_id) === Number(state.user?.id) && state.duelSelectedButton) {
        state.duelSelectedButton.classList.remove("pending");
        state.duelSelectedButton.classList.add(data.correct ? "correct" : "wrong");
      }
      feedback(
        els.duelStatus,
        data.correct ? `Answered correctly: ${data.correct_answer}` : `Answer in: ${data.correct_answer}`,
        data.correct,
      );
    }
    if (data.type === "duel_peer_answered" && Number(data.user_id) !== Number(state.user?.id)) {
      feedback(els.duelStatus, `Opponent answered. Waiting for round result (${data.answered_count}/${data.player_count}).`, true);
    }
    if (data.type === "duel_round_result") {
      renderDuelScores(data.scores);
      feedback(els.duelStatus, `Round answer: ${data.correct_answer}`, true);
    }
    if (data.type === "duel_complete") {
      renderDuelScores(data.scores);
      els.startDuel.disabled = false;
      els.duelRound.hidden = true;
      const youWon = Number(data.winner_id) === Number(state.user?.id);
      const tied = !data.winner_id;
      feedback(els.duelStatus, tied ? "Duel complete: draw." : youWon ? "Duel complete: you won." : "Duel complete: opponent won.", youWon || tied);
    }
  });
  socket.addEventListener("close", () => {
    if (state.duelSocket === socket) state.duelSocket = null;
  });
}

function closeDuelSocket() {
  if (state.duelSocket && state.duelSocket.readyState <= WebSocket.OPEN) {
    state.duelSocket.close();
  }
  state.duelSocket = null;
}

function startDuel() {
  if (!state.duelSocket || state.duelSocket.readyState !== WebSocket.OPEN) {
    feedback(els.duelStatus, "Join a duel room first.", false);
    return;
  }
  els.startDuel.disabled = true;
  state.duelSocket.send(JSON.stringify({ type: "start", format: els.duelFormat.value }));
}

function renderDuelQuestion(question) {
  state.duelAnswered = false;
  state.duelSelectedButton = null;
  els.duelRound.hidden = false;
  els.duelRoundLabel.textContent = `Round ${question.round}/${question.total}`;
  els.duelPrompt.textContent = question.prompt.text;
  renderDuelScores(question.scores);
  els.duelGrid.innerHTML = "";
  question.choices.forEach((choice, index) => {
    const button = document.createElement("button");
    button.className = "flag-choice";
    button.type = "button";
    button.dataset.choiceId = choice.id;
    button.style.setProperty("--i", index);
    button.innerHTML = `<img alt="Duel flag option" src="${escapeAttr(choice.flag_url)}">`;
    button.addEventListener("click", () => answerDuel(choice.id, button));
    els.duelGrid.appendChild(button);
  });
  feedback(els.duelStatus, "Pick the correct flag before your opponent.", true);
}

function answerDuel(choiceId, button) {
  if (state.duelAnswered || !state.duelSocket || state.duelSocket.readyState !== WebSocket.OPEN) return;
  state.duelAnswered = true;
  state.duelSelectedButton = button;
  els.duelGrid.querySelectorAll("button").forEach((node) => {
    node.disabled = true;
  });
  button.classList.add("pending");
  state.duelSocket.send(JSON.stringify({ type: "answer", choice_id: choiceId }));
}

function renderDuelScores(scores = {}) {
  const myId = String(state.user?.id || "");
  const entries = Object.entries(scores);
  const myScore = Number(scores[myId] || 0);
  const opponent = entries.find(([id]) => id !== myId);
  els.duelYou.textContent = myScore;
  els.duelThem.textContent = opponent ? Number(opponent[1] || 0) : 0;
}

async function loadProfile() {
  renderProfileSkeleton();
  try {
    const stats = await api("/api/profile/stats");
    const user = stats.user || {};
    const answers = stats.answers || {};
    const total = Number(answers.total_answers || 0);
    const correct = Number(answers.correct_answers || 0);
    const missed = Math.max(0, total - correct);
    const accuracy = total ? Math.round((correct / total) * 100) : 0;
    const name = user.username || state.user?.first_name || "Profile";
    els.profileName.textContent = name;
    els.profileAvatar.textContent = initials(name);
    els.totalAnswers.textContent = total;
    els.accuracy.textContent = `${accuracy}%`;
    els.avgTime.textContent = answers.avg_correct_ms ? `${(answers.avg_correct_ms / 1000).toFixed(1)}s` : "-";
    els.bestStreak.textContent = user.max_streak || 0;
    els.correctBar.style.height = `${Math.max(8, correct ? (correct / Math.max(total, 1)) * 100 : 8)}%`;
    els.missedBar.style.height = `${Math.max(8, missed ? (missed / Math.max(total, 1)) * 100 : 8)}%`;
    renderBadges(stats.badges || []);
  } catch (error) {
    renderError(els.badges, error.message || "Could not load profile.");
  }
}

function renderProfileSkeleton() {
  els.totalAnswers.textContent = "-";
  els.accuracy.textContent = "-";
  els.avgTime.textContent = "-";
  els.bestStreak.textContent = "-";
  els.badges.innerHTML = `<span class="badge">Loading chart...</span>`;
}

function renderBadges(badges) {
  els.badges.innerHTML = "";
  if (!badges.length) {
    els.badges.innerHTML = '<div class="empty-state">Badges appear here after real server-side unlocks.</div>';
    return;
  }
  badges.forEach((badge) => {
    const node = document.createElement("span");
    node.className = "badge";
    node.textContent = `${iconForBadge(badge.icon)} ${badge.name}`;
    els.badges.appendChild(node);
  });
}

async function loadLeaderboard() {
  els.leaders.innerHTML = skeletonRows(5);
  els.rankWindow.innerHTML = "";
  try {
    const data = await api(`/api/leaderboard?scope=${state.leaderboardScope}`);
    renderLeaderList(els.leaders, data.leaders || [], false);
    renderLeaderList(els.rankWindow, data.you || [], true);
  } catch (error) {
    renderError(els.leaders, error.message || "Could not load leaderboard.");
  }
}

function renderLeaderList(container, rows, markYou) {
  container.innerHTML = "";
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">No scores on this board yet.</div>';
    return;
  }
  rows.forEach((row) => {
    const isYou = Number(row.telegram_id) === Number(state.user?.id);
    const el = document.createElement("div");
    el.className = `leader-row top-${row.rank <= 3 ? row.rank : 0} ${markYou && isYou ? "you" : ""}`;
    el.innerHTML = `
      <div class="avatar">${initials(row.username)}</div>
      <div class="leader-name">#${row.rank} ${escapeHtml(row.username || "Player")}</div>
      <div class="leader-score">${row.score} pts</div>
    `;
    container.appendChild(el);
  });
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (!options.skipAuth && state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(error.detail || "Request failed");
  }
  return response.json();
}

function startTimer() {
  state.timerDeadline = Date.now() + state.timerTotal * 1000;
  paintTimer(state.timerTotal);
  state.timerId = window.setInterval(() => {
    const remaining = Math.max(0, (state.timerDeadline - Date.now()) / 1000);
    paintTimer(remaining);
    if (remaining <= 0) {
      stopTimer();
      loadGridQuestion();
    }
  }, 250);
}

function stopTimer() {
  if (state.timerId) window.clearInterval(state.timerId);
  state.timerId = null;
  document.querySelector(".timer-ring")?.classList.remove("low");
}

function paintTimer(remaining) {
  const ratio = Math.max(0, remaining / state.timerTotal);
  const circumference = 169.65;
  const hue = ratio > 0.5 ? 145 : ratio > 0.24 ? 44 : 5;
  els.timerProgress.style.strokeDashoffset = `${circumference * (1 - ratio)}`;
  els.timerProgress.style.stroke = `hsl(${hue} 68% 45%)`;
  els.timerText.textContent = Math.ceil(remaining);
  document.querySelector(".timer-ring")?.classList.toggle("low", remaining <= 3 && remaining > 0);
}

function disableGrid(disabled) {
  els.flagGrid.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
  });
}

function feedback(el, text, good) {
  el.textContent = text;
  el.classList.toggle("good", good);
  el.classList.toggle("bad", !good);
}

function clearFeedback(el) {
  el.textContent = "";
  el.classList.remove("good", "bad");
}

function successFeedback() {
  tg?.HapticFeedback?.notificationOccurred("success");
}

function errorFeedback() {
  tg?.HapticFeedback?.notificationOccurred("error");
  window.setTimeout(() => tg?.HapticFeedback?.impactOccurred("light"), 120);
}

function lightFeedback() {
  tg?.HapticFeedback?.impactOccurred("light");
}

function burstConfetti() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const canvas = els.confetti;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = window.innerWidth * dpr;
  canvas.height = window.innerHeight * dpr;
  ctx.scale(dpr, dpr);
  const colors = ["#1f8f5f", "#0f7f7a", "#d4a62a", "#d95f34"];
  const pieces = Array.from({ length: 28 }, () => ({
    x: window.innerWidth / 2,
    y: window.innerHeight * 0.2,
    vx: (Math.random() - 0.5) * 7,
    vy: Math.random() * -5 - 2,
    size: Math.random() * 5 + 3,
    color: colors[Math.floor(Math.random() * colors.length)],
    life: 38,
  }));
  function frame() {
    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    pieces.forEach((p) => {
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.22;
      p.life -= 1;
      ctx.fillStyle = p.color;
      ctx.fillRect(p.x, p.y, p.size, p.size);
    });
    if (pieces.some((p) => p.life > 0)) requestAnimationFrame(frame);
    else ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  }
  frame();
}

function showBadgeToast(badge) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = `${iconForBadge(badge.icon)} Badge unlocked: ${badge.name}`;
  els.toastStack.appendChild(toast);
  window.setTimeout(() => toast.remove(), 3200);
}

function renderError(container, message) {
  container.hidden = false;
  container.innerHTML = `<div class="error-state">${escapeHtml(message)}</div>`;
}

function skeletonChoices(count) {
  return Array.from({ length: count }, (_, index) => `<div class="flag-choice skeleton" style="--i:${index}"></div>`).join("");
}

function skeletonCards(count) {
  return Array.from({ length: count }, (_, index) => `<div class="card skeleton" style="--i:${index}"></div>`).join("");
}

function skeletonRows(count) {
  return Array.from({ length: count }, () => `
    <div class="leader-row">
      <div class="skeleton avatar-skeleton"></div>
      <div class="skeleton line wide"></div>
      <div class="skeleton line"></div>
    </div>
  `).join("");
}

function titleFor(view) {
  return { grid: "Quiz", daily: "Daily", duel: "Duel", profile: "Profile", leaderboard: "Ranks" }[view] || "Flag Atlas";
}

function labelForCategory(key) {
  const pack = state.gameOptions?.categories?.find((item) => item.key === key);
  return pack?.label || "World flags";
}

function iconForBadge(icon) {
  return { bolt: "[fast]", flame: "[streak]", medal: "[medal]" }[icon] || "[badge]";
}

function initials(name = "P") {
  return String(name)
    .split(/[_\s]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function randomKey() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function kebab(value) {
  return value.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`).replace(/_/g, "-");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function escapeAttr(value) {
  return escapeHtml(value);
}
