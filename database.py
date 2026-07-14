import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "flasgs_bot.sqlite3"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                     telegram_id INTEGER PRIMARY KEY,
                     username TEXT,
                     score INTEGER DEFAULT 0,
                     streak INTEGER DEFAULT 0,
                     max_streak INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS challenges (
                     challenge_id TEXT PRIMARY KEY,
                     creator_id INTEGER,
                     creator_username TEXT,
                     opponent_id INTEGER DEFAULT NULL,
                     opponent_username TEXT DEFAULT NULL,
                     continent TEXT,
                     countries_list TEXT,
                     creator_score INTEGER DEFAULT NULL,
                     opponent_score INTEGER DEFAULT NULL,
                     quiz_type TEXT DEFAULT 'flag',
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            conn.execute("ALTER TABLE challenges ADD COLUMN quiz_type TEXT DEFAULT 'flag'")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    run_migrations()

def run_migrations():
    if not MIGRATIONS_DIR.exists():
        return
    with get_db_connection() as conn:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))
        conn.commit()

def ensure_user(telegram_id: int, username: str):
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM USERS WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not user:
            conn.execute("INSERT INTO users (telegram_id, username) VALUES (?, ?)", (telegram_id, username))
            conn.commit()
         
def get_user_stats(telegram_id: int):
    with get_db_connection() as conn:
        return conn.execute(
"SELECT score, streak, max_streak FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    
def update_score(telegram_id: int, is_correct: bool, username: str = "Player"):
    ensure_user(telegram_id, username)
    stats = get_user_stats(telegram_id)
    if not stats:
        return {"score": 0, "streak": 0, "max_streak": 0}
    
    score = stats["score"]
    streak = stats["streak"]
    max_streak = stats["max_streak"]

    if is_correct:
        score += 1
        streak += 1
        if streak > max_streak:
            max_streak = streak
    else:
        streak = 0

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET score = ?, streak = ?, max_streak = ? WHERE telegram_id = ?",
            (score, streak, max_streak, telegram_id)
        )
        conn.commit()
    
    return {"score": score, "streak": streak, "max_streak": max_streak}
    
def get_leaderboard(limit: int = 10):
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT username, score, streak, max_streak FROM users ORDER BY score DESC LIMIT ?",
            (limit,)
        ).fetchall()

def create_challenge(challenge_id: str, creator_id: int, creator_username: str, continent: str, countries_list: list, quiz_type: str = "flag"):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO challenges (challenge_id, creator_id, creator_username, continent, countries_list, quiz_type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (challenge_id, creator_id, creator_username, continent, json.dumps(countries_list), quiz_type)
        )
        conn.commit()

def get_challenge(challenge_id: str):
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
        if row:
            res = dict(row)
            res["countries_list"] = json.loads(res["countries_list"])
            return res
        return None

def update_challenge_creator_score(challenge_id: str, score: int):
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE challenges SET creator_score = ? WHERE challenge_id = ?",
            (score, challenge_id)
        )
        conn.commit()

def join_challenge_opponent(challenge_id: str, opponent_id: int, opponent_username: str):
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE challenges SET opponent_id = ?, opponent_username = ? WHERE challenge_id = ?",
            (opponent_id, opponent_username, challenge_id)
        )
        conn.commit()

def update_challenge_opponent_score(challenge_id: str, score: int):
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE challenges SET opponent_score = ? WHERE challenge_id = ?",
            (score, challenge_id)
        )
        conn.commit()

def create_question_session(
    question_id: str,
    telegram_id: int,
    mode: str,
    prompt: str,
    correct_country: str,
    choices: list,
):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO tma_question_sessions
                (question_id, telegram_id, mode, prompt, correct_country, choices_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (question_id, telegram_id, mode, prompt, correct_country, json.dumps(choices)),
        )
        conn.commit()

def get_question_session(question_id: str, telegram_id: int):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM tma_question_sessions
            WHERE question_id = ? AND telegram_id = ?
            """,
            (question_id, telegram_id),
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        res["choices"] = json.loads(res["choices_json"])
        return res

def mark_question_answered(question_id: str, telegram_id: int):
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE tma_question_sessions
            SET answered_at = CURRENT_TIMESTAMP
            WHERE question_id = ? AND telegram_id = ?
            """,
            (question_id, telegram_id),
        )
        conn.commit()

def log_quiz_answer(
    telegram_id: int,
    username: str,
    mode: str,
    question_id: str,
    prompt: str,
    selected_answer: str,
    correct_answer: str,
    is_correct: bool,
    response_ms: int | None,
):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO quiz_answers_log
                (telegram_id, username, mode, question_id, prompt, selected_answer,
                 correct_answer, is_correct, response_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                username,
                mode,
                question_id,
                prompt,
                selected_answer,
                correct_answer,
                1 if is_correct else 0,
                response_ms,
            ),
        )
        conn.commit()

def get_profile_stats(telegram_id: int):
    with get_db_connection() as conn:
        user = conn.execute(
            """
            SELECT telegram_id, username, score, streak, max_streak
            FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS total_answers,
                COALESCE(SUM(is_correct), 0) AS correct_answers,
                AVG(CASE WHEN is_correct = 1 THEN response_ms END) AS avg_correct_ms
            FROM quiz_answers_log
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
        duel_totals = conn.execute(
            """
            SELECT
                COUNT(*) AS completed_duels,
                COALESCE(SUM(CASE
                    WHEN (creator_id = ? AND creator_score > opponent_score)
                      OR (opponent_id = ? AND opponent_score > creator_score)
                    THEN 1 ELSE 0 END), 0) AS wins,
                COALESCE(SUM(CASE
                    WHEN (creator_id = ? AND creator_score < opponent_score)
                      OR (opponent_id = ? AND opponent_score < creator_score)
                    THEN 1 ELSE 0 END), 0) AS losses
            FROM duel_sessions
            WHERE status = 'completed' AND (creator_id = ? OR opponent_id = ?)
            """,
            (telegram_id, telegram_id, telegram_id, telegram_id, telegram_id, telegram_id),
        ).fetchone()
        badges = conn.execute(
            """
            SELECT b.badge_id, b.name, b.description, ub.unlocked_at
            FROM user_badges ub
            JOIN badges b ON b.badge_id = ub.badge_id
            WHERE ub.telegram_id = ?
            ORDER BY ub.unlocked_at DESC
            """,
            (telegram_id,),
        ).fetchall()
        return {
            "user": dict(user) if user else None,
            "answers": dict(totals),
            "duels": dict(duel_totals),
            "badges": [dict(row) for row in badges],
        }

def get_leaderboard_with_ranks(limit: int = 10):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT telegram_id, username, score, streak, max_streak,
                   RANK() OVER (ORDER BY score DESC, max_streak DESC, telegram_id ASC) AS rank
            FROM users
            ORDER BY score DESC, max_streak DESC, telegram_id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

def get_user_rank_window(telegram_id: int, radius: int = 2):
    with get_db_connection() as conn:
        ranked = conn.execute(
            """
            WITH ranked AS (
                SELECT telegram_id, username, score, streak, max_streak,
                       RANK() OVER (ORDER BY score DESC, max_streak DESC, telegram_id ASC) AS rank
                FROM users
            ),
            me AS (
                SELECT rank FROM ranked WHERE telegram_id = ?
            )
            SELECT ranked.*
            FROM ranked, me
            WHERE ranked.rank BETWEEN me.rank - ? AND me.rank + ?
            ORDER BY ranked.rank ASC
            """,
            (telegram_id, radius, radius),
        ).fetchall()
        return [dict(row) for row in ranked]

def create_duel_session(duel_id: str, creator_id: int, challenge_id: str | None = None):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO duel_sessions (duel_id, challenge_id, creator_id)
            VALUES (?, ?, ?)
            """,
            (duel_id, challenge_id, creator_id),
        )
        conn.commit()

def join_duel_session(duel_id: str, opponent_id: int):
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE duel_sessions
            SET opponent_id = ?, status = 'ready'
            WHERE duel_id = ? AND opponent_id IS NULL
            """,
            (opponent_id, duel_id),
        )
        conn.commit()

def get_duel_session(duel_id: str):
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM duel_sessions WHERE duel_id = ?", (duel_id,)).fetchone()
        return dict(row) if row else None

def log_duel_event(duel_id: str, telegram_id: int, event_type: str, payload: dict):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO duel_events (duel_id, telegram_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (duel_id, telegram_id, event_type, json.dumps(payload)),
        )
        conn.commit()
