PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS tma_question_sessions (
    question_id TEXT PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    prompt TEXT,
    correct_country TEXT,
    choices_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    answered_at TIMESTAMP DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_tma_question_sessions_user_created
    ON tma_question_sessions (telegram_id, created_at);

CREATE TABLE IF NOT EXISTS quiz_answers_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    username TEXT,
    mode TEXT NOT NULL,
    question_id TEXT,
    prompt TEXT,
    selected_answer TEXT,
    correct_answer TEXT,
    is_correct INTEGER NOT NULL,
    response_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_quiz_answers_log_user_created
    ON quiz_answers_log (telegram_id, created_at);

CREATE TABLE IF NOT EXISTS badges (
    badge_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    condition_key TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_badges (
    telegram_id INTEGER NOT NULL,
    badge_id TEXT NOT NULL,
    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (telegram_id, badge_id),
    FOREIGN KEY (badge_id) REFERENCES badges (badge_id)
);

CREATE TABLE IF NOT EXISTS duel_sessions (
    duel_id TEXT PRIMARY KEY,
    challenge_id TEXT,
    status TEXT NOT NULL DEFAULT 'waiting',
    creator_id INTEGER NOT NULL,
    opponent_id INTEGER,
    current_question_id TEXT,
    creator_score INTEGER NOT NULL DEFAULT 0,
    opponent_score INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP DEFAULT NULL,
    completed_at TIMESTAMP DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_duel_sessions_status
    ON duel_sessions (status, created_at);

CREATE TABLE IF NOT EXISTS duel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    duel_id TEXT NOT NULL,
    telegram_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (duel_id) REFERENCES duel_sessions (duel_id)
);

CREATE INDEX IF NOT EXISTS idx_duel_events_duel_created
    ON duel_events (duel_id, created_at);

INSERT OR IGNORE INTO badges (badge_id, name, description, condition_key)
VALUES
    ('speed_demon', 'Speed Demon', 'Average correct response under 2 seconds.', 'avg_correct_under_2000'),
    ('streak_starter', 'Streak Starter', 'Reach a streak of 5.', 'max_streak_5'),
    ('europe_master', 'Europe Master', 'Answer 25 Europe questions correctly.', 'europe_correct_25');
