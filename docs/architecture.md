# Telegram Mini App Architecture

## Current repo facts

- The bot is a single `python-telegram-bot` polling process in `bot.py`.
- The SQLite database path in code is `flasgs_bot.sqlite3` and the file exists with that spelling.
- Existing tables are `users` and `challenges`; no quiz answer history table existed before this migration.
- Country/flag data is in `countries.py` and currently points at FlagCDN PNG URLs. The TMA derives SVG URLs from those existing FlagCDN URLs when possible.

## Topology

Use a single Python process for now:

- PTB keeps polling for `/start`, `/quiz`, `/challenge`, `/play`, and existing callback buttons.
- FastAPI runs in a background thread in the same process and serves both `/api/*` and `/tma/*`.
- Static frontend files live in `tma/`.

This keeps deployment simple for a solo-maintained bot. If traffic grows, split FastAPI and PTB into sibling services that share the same database or a future Postgres instance.

## Frontend choice

Phase 1 uses static HTML/CSS/JavaScript rather than React/Vite. The reason is operational simplicity: the current project has no Node build pipeline, and Telegram can load the app directly from files served by FastAPI.

React remains a good future upgrade once the TMA grows past a few modes or needs stronger component/state boundaries.

## Backend API

FastAPI endpoints:

- `POST /api/auth/telegram`
- `GET /api/quiz/question?mode=grid|match`
- `POST /api/quiz/answer`
- `GET /api/profile/stats`
- `GET /api/leaderboard?scope=global`
- `POST /api/challenge/create`
- `POST /api/challenge/join`
- `WS /ws/duel/{duel_id}`

Telegram `initData` is validated server-side using the bot token HMAC flow before issuing a short-lived JWT session token. Scoring endpoints require that token.

## Real-time transport

WebSockets are the selected real-time transport. FastAPI supports them directly, and a polling fallback can be added later if the chosen host does not proxy WebSockets reliably.

The current WebSocket endpoint is a phase-3 foundation: it handles room presence and event broadcasting. The synchronized race question state should be promoted from in-memory connection state into `duel_sessions` and `duel_events` before production duel launches.

## Database path and migration

SQLite remains the source of truth for now. Migration `migrations/001_tma_tables.sql` is additive and safe to rerun.

New tables:

- `tma_question_sessions`
- `quiz_answers_log`
- `badges`
- `user_badges`
- `duel_sessions`
- `duel_events`

Postgres migration path: keep DB access behind `database.py` for now, then replace the implementation with SQLAlchemy models/session handling when concurrency or hosting requires Postgres.

## Phased delivery

1. Phase 1: FastAPI, Telegram auth, session token, Grid of Flags TMA mode.
2. Phase 2: Flag Matching mode, profile stats, leaderboard.
3. Phase 3: Real-time multiplayer duel state over WebSockets.
4. Phase 4: Tap-the-Map mode, backend badge rule evaluator, sound/music polish.

## Assumptions

- `TMA_PUBLIC_URL` will be an HTTPS URL in production; Telegram requires HTTPS Mini App URLs.
- Local browser testing can use `TMA_DEV_AUTH=1`, but production must leave it disabled.
- FlagCDN assets are acceptable to keep using because the existing bot already depends on them.
- Current SQLite scale is acceptable for early TMA use; WebSocket multiplayer at scale should move to Postgres or Redis-backed shared state.
