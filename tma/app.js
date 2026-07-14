const tg = window.Telegram?.WebApp;
const state = {
  token: "",
  user: null,
  view: "grid",
  gridQuestion: null,
  matchQuestion: null,
  flippedCards: [],
  matchedCards: new Set(),
  timerId: null,
  timerTotal: 15,
};

const els = {
  authPanel: document.getElementById("auth-panel"),
  screenTitle: document.getElementById("screen-title"),
  scoreValue: document.getElementById("score-value"),
  tabs: document.querySelectorAll(".tab"),
  views: document.querySelectorAll(".view"),
  gridPrompt: document.getElementById("grid-prompt"),
  flagGrid: document.getElementById("flag-grid"),
  gridFeedback: document.getElementById("grid-feedback"),
  gridNext: document.getElementById("grid-next"),
  timerRing: document.getElementById("timer-ring"),
  timerText: document.getElementById("timer-text"),
  matchBoard: document.getElementById("match-board"),
  matchFeedback: document.getElementById("match-feedback"),
  matchNew: document.getElementById("match-new"),
  profileName: document.getElementById("profile-name"),
  totalAnswers: document.getElementById("total-answers"),
  accuracy: document.getElementById("accuracy"),
  avgTime: document.getElementById("avg-time"),
  bestStreak: document.getElementById("best-streak"),
  correctBar: document.getElementById("correct-bar"),
  missedBar: document.getElementById("missed-bar"),
  badges: document.getElementById("badges"),
  leaders: document.getElementById("leaders"),
  rankWindow: document.getElementById("rank-window"),
  confetti: document.getElementById("confetti-canvas"),
};

boot();

async function boot() {
  tg?.ready();
  tg?.expand();
  applyTelegramTheme();
  bindEvents();
  await authenticate();
  els.authPanel.hidden = true;
  showView("grid");
  await loadGridQuestion();
}

function applyTelegramTheme() {
  if (!tg?.themeParams) return;
  const root = document.documentElement;
  Object.entries(tg.themeParams).forEach(([key, value]) => {
    root.style.setProperty(`--tg-theme-${kebab(key)}`, value);
  });
}

function bindEvents() {
  els.tabs.forEach((tab) => {
    tab.addEventListener("click", async () => {
      await showView(tab.dataset.view);
    });
  });
  els.gridNext.addEventListener("click", loadGridQuestion);
  els.matchNew.addEventListener("click", loadMatchDeck);
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

async function showView(view) {
  state.view = view;
  els.screenTitle.textContent = titleFor(view);
  els.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  els.views.forEach((section) => {
    section.hidden = section.id !== `${view}-view`;
    section.classList.toggle("active", !section.hidden);
  });
  if (view === "match" && !state.matchQuestion) await loadMatchDeck();
  if (view === "profile") await loadProfile();
  if (view === "leaderboard") await loadLeaderboard();
}

async function loadGridQuestion() {
  stopTimer();
  clearFeedback(els.gridFeedback);
  els.flagGrid.innerHTML = "";
  const question = await api("/api/quiz/question?mode=grid&choices_count=6");
  state.gridQuestion = question;
  els.gridPrompt.textContent = question.prompt.text;
  question.choices.forEach((choice) => {
    const button = document.createElement("button");
    button.className = "flag-choice";
    button.type = "button";
    button.dataset.choiceId = choice.id;
    button.innerHTML = `<img alt="Flag option" src="${escapeAttr(choice.flag_url)}">`;
    button.addEventListener("click", () => answerGrid(choice.id, button));
    els.flagGrid.appendChild(button);
  });
  startTimer();
}

async function answerGrid(choiceId, button) {
  stopTimer();
  disableGrid(true);
  const result = await api("/api/quiz/answer", {
    method: "POST",
    body: JSON.stringify({ question_id: state.gridQuestion.question_id, choice_id: choiceId }),
  });
  els.scoreValue.textContent = result.stats.score;
  if (result.correct) {
    button.classList.add("correct");
    feedback(els.gridFeedback, `Correct: ${result.correct_answer}`, true);
    successFeedback();
    burstConfetti();
  } else {
    button.classList.add("wrong");
    feedback(els.gridFeedback, `Missed: ${result.correct_answer}`, false);
    errorFeedback();
  }
  window.setTimeout(loadGridQuestion, 1100);
}

async function loadMatchDeck() {
  clearFeedback(els.matchFeedback);
  state.flippedCards = [];
  state.matchedCards = new Set();
  els.matchBoard.innerHTML = "";
  const deck = await api("/api/quiz/question?mode=match");
  state.matchQuestion = deck;
  deck.cards.forEach((card) => els.matchBoard.appendChild(renderCard(card)));
}

function renderCard(card) {
  const button = document.createElement("button");
  button.className = "card";
  button.type = "button";
  button.dataset.cardId = card.id;
  button.innerHTML = `
    <span class="card-inner">
      <span class="card-face card-front">?</span>
      <span class="card-face card-back">${card.kind === "flag" ? `<img alt="Flag card" src="${escapeAttr(card.flag_url)}">` : escapeHtml(card.label)}</span>
    </span>
  `;
  button.addEventListener("click", () => flipCard(button));
  return button;
}

async function flipCard(cardEl) {
  if (cardEl.classList.contains("matched") || cardEl.classList.contains("flipped")) return;
  if (state.flippedCards.length >= 2) return;
  cardEl.classList.add("flipped");
  lightFeedback();
  state.flippedCards.push(cardEl);
  if (state.flippedCards.length !== 2) return;

  const cardIds = state.flippedCards.map((el) => el.dataset.cardId);
  const result = await api("/api/quiz/answer", {
    method: "POST",
    body: JSON.stringify({ question_id: state.matchQuestion.question_id, card_ids: cardIds }),
  });
  els.scoreValue.textContent = result.stats.score;

  if (result.correct) {
    state.flippedCards.forEach((el) => {
      el.classList.add("matched");
      state.matchedCards.add(el.dataset.cardId);
    });
    feedback(els.matchFeedback, "Matched", true);
    successFeedback();
    if (state.matchedCards.size === state.matchQuestion.cards.length) {
      burstConfetti();
      window.setTimeout(loadMatchDeck, 1000);
    }
  } else {
    feedback(els.matchFeedback, "Try another pair", false);
    errorFeedback();
    window.setTimeout(() => {
      state.flippedCards.forEach((el) => el.classList.remove("flipped"));
    }, 650);
  }
  window.setTimeout(() => {
    state.flippedCards = [];
  }, 700);
}

async function loadProfile() {
  const stats = await api("/api/profile/stats");
  const user = stats.user || {};
  const answers = stats.answers || {};
  const total = Number(answers.total_answers || 0);
  const correct = Number(answers.correct_answers || 0);
  const missed = Math.max(0, total - correct);
  const accuracy = total ? Math.round((correct / total) * 100) : 0;
  els.profileName.textContent = user.username || state.user?.first_name || "Profile";
  els.totalAnswers.textContent = total;
  els.accuracy.textContent = `${accuracy}%`;
  els.avgTime.textContent = answers.avg_correct_ms ? `${(answers.avg_correct_ms / 1000).toFixed(1)}s` : "-";
  els.bestStreak.textContent = user.max_streak || 0;
  els.correctBar.style.height = `${Math.max(8, correct ? (correct / Math.max(total, 1)) * 100 : 8)}%`;
  els.missedBar.style.height = `${Math.max(8, missed ? (missed / Math.max(total, 1)) * 100 : 8)}%`;
  els.badges.innerHTML = "";
  const badges = stats.badges || [];
  if (!badges.length) {
    els.badges.innerHTML = '<span class="badge">No badges yet</span>';
  } else {
    badges.forEach((badge) => {
      const node = document.createElement("span");
      node.className = "badge";
      node.textContent = badge.name;
      els.badges.appendChild(node);
    });
  }
}

async function loadLeaderboard() {
  const data = await api("/api/leaderboard?scope=global");
  renderLeaderList(els.leaders, data.leaders || []);
  renderLeaderList(els.rankWindow, data.you || []);
}

function renderLeaderList(container, rows) {
  container.innerHTML = "";
  if (!rows.length) {
    container.innerHTML = '<p class="feedback">No scores yet.</p>';
    return;
  }
  rows.forEach((row) => {
    const el = document.createElement("div");
    el.className = `leader-row top-${row.rank <= 3 ? row.rank : 0}`;
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
  let remaining = state.timerTotal;
  paintTimer(remaining);
  state.timerId = window.setInterval(() => {
    remaining -= 1;
    paintTimer(remaining);
    if (remaining <= 0) {
      stopTimer();
      loadGridQuestion();
    }
  }, 1000);
}

function stopTimer() {
  if (state.timerId) window.clearInterval(state.timerId);
  state.timerId = null;
}

function paintTimer(remaining) {
  const ratio = Math.max(0, remaining / state.timerTotal);
  const hue = ratio > 0.5 ? 135 : ratio > 0.25 ? 48 : 0;
  els.timerRing.style.setProperty("--timer-deg", `${ratio * 360}deg`);
  els.timerRing.style.background = `conic-gradient(hsl(${hue} 70% 42%) ${ratio * 360}deg, color-mix(in srgb, var(--muted) 18%, transparent) 0deg)`;
  els.timerText.textContent = remaining;
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
  const canvas = els.confetti;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = window.innerWidth * dpr;
  canvas.height = window.innerHeight * dpr;
  ctx.scale(dpr, dpr);
  const colors = ["#1f9d67", "#2481cc", "#d7a514", "#dc3f4d"];
  const pieces = Array.from({ length: 42 }, () => ({
    x: window.innerWidth / 2,
    y: window.innerHeight * 0.22,
    vx: (Math.random() - 0.5) * 8,
    vy: Math.random() * -5 - 2,
    size: Math.random() * 5 + 4,
    color: colors[Math.floor(Math.random() * colors.length)],
    life: 52,
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

function titleFor(view) {
  return { grid: "Grid", match: "Match", profile: "Profile", leaderboard: "Ranks" }[view] || "Flag Rush";
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
