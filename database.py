import sqlite3
import json
from pathlib import Path

import game_logic

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
    ensure_runtime_schema()

def run_migrations():
    if not MIGRATIONS_DIR.exists():
        return
    with get_db_connection() as conn:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))
        conn.commit()

def ensure_runtime_schema():
    with get_db_connection() as conn:
        ensure_column(conn, "tma_question_sessions", "category", "TEXT DEFAULT 'all'")
        ensure_column(conn, "tma_question_sessions", "difficulty", "TEXT DEFAULT 'medium'")
        ensure_column(conn, "tma_question_sessions", "question_kind", "TEXT DEFAULT 'country'")
        ensure_column(conn, "tma_question_sessions", "timer_seconds", "INTEGER DEFAULT 15")
        ensure_column(conn, "quiz_answers_log", "country_name", "TEXT")
        ensure_column(conn, "quiz_answers_log", "continent", "TEXT")
        ensure_column(conn, "quiz_answers_log", "category", "TEXT DEFAULT 'all'")
        ensure_column(conn, "quiz_answers_log", "difficulty", "TEXT DEFAULT 'medium'")
        ensure_column(conn, "quiz_answers_log", "points_awarded", "INTEGER DEFAULT 0")
        ensure_column(conn, "quiz_answers_log", "streak_after", "INTEGER DEFAULT 0")
        ensure_column(conn, "quiz_answers_log", "multiplier", "REAL DEFAULT 1.0")
        ensure_column(conn, "quiz_answers_log", "is_suspicious", "INTEGER DEFAULT 0")
        ensure_column(conn, "badges", "icon", "TEXT DEFAULT 'medal'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS answer_receipts (
                idempotency_key TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matchmaking_queue (
                telegram_id INTEGER PRIMARY KEY,
                format TEXT NOT NULL DEFAULT 'bo5',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_matchmaking_queue_created
            ON matchmaking_queue (created_at)
        """)
        conn.executemany(
            """
            INSERT INTO badges (badge_id, name, description, condition_key, icon)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(badge_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                icon = excluded.icon
            """,
            game_logic.badge_seed_rows(),
        )
        conn.commit()

def ensure_column(conn, table: str, column: str, definition: str):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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

def apply_score(
    telegram_id: int,
    is_correct: bool,
    points: int,
    next_streak: int,
    username: str = "Player",
):
    ensure_user(telegram_id, username)
    stats = get_user_stats(telegram_id)
    if not stats:
        return {"score": 0, "streak": 0, "max_streak": 0}
    score = int(stats["score"]) + (points if is_correct else 0)
    max_streak = max(int(stats["max_streak"]), next_streak)
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET score = ?, streak = ?, max_streak = ? WHERE telegram_id = ?",
            (score, next_streak, max_streak, telegram_id),
        )
        conn.commit()
    return {"score": score, "streak": next_streak, "max_streak": max_streak}
    
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
    category: str = "all",
    difficulty: str = "medium",
    question_kind: str = "country",
    timer_seconds: int = 15,
):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO tma_question_sessions
                (question_id, telegram_id, mode, prompt, correct_country, choices_json,
                 category, difficulty, question_kind, timer_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                telegram_id,
                mode,
                prompt,
                correct_country,
                json.dumps(choices),
                category,
                difficulty,
                question_kind,
                timer_seconds,
            ),
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
    country_name: str | None = None,
    continent: str | None = None,
    category: str = "all",
    difficulty: str = "medium",
    points_awarded: int = 0,
    streak_after: int = 0,
    multiplier: float = 1.0,
    is_suspicious: bool = False,
):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO quiz_answers_log
                (telegram_id, username, mode, question_id, prompt, selected_answer,
                 correct_answer, is_correct, response_ms, country_name, continent,
                 category, difficulty, points_awarded, streak_after, multiplier, is_suspicious)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                country_name,
                continent,
                category,
                difficulty,
                points_awarded,
                streak_after,
                multiplier,
                1 if is_suspicious else 0,
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
            SELECT b.badge_id, b.name, b.description, b.icon, ub.unlocked_at
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

def get_user_country_performance(telegram_id: int):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT country_name,
                   COUNT(*) AS attempts,
                   COALESCE(SUM(CASE WHEN is_correct = 0 THEN 1 ELSE 0 END), 0) AS misses
            FROM quiz_answers_log
            WHERE telegram_id = ? AND country_name IS NOT NULL AND is_suspicious = 0
            GROUP BY country_name
            """,
            (telegram_id,),
        ).fetchall()
        return {row["country_name"]: {"attempts": row["attempts"], "misses": row["misses"]} for row in rows}

def get_answer_receipt(idempotency_key: str, telegram_id: int):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT response_json FROM answer_receipts
            WHERE idempotency_key = ? AND telegram_id = ?
            """,
            (idempotency_key, telegram_id),
        ).fetchone()
        return json.loads(row["response_json"]) if row else None

def save_answer_receipt(idempotency_key: str, telegram_id: int, question_id: str, response: dict):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO answer_receipts
                (idempotency_key, telegram_id, question_id, response_json)
            VALUES (?, ?, ?, ?)
            """,
            (idempotency_key, telegram_id, question_id, json.dumps(response)),
        )
        conn.commit()

def get_previous_grid_response(question_id: str, telegram_id: int):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM quiz_answers_log
            WHERE question_id = ? AND telegram_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (question_id, telegram_id),
        ).fetchone()
        if not row:
            return None
        stats = get_user_stats(telegram_id)
        return {
            "correct": bool(row["is_correct"]),
            "completed": True,
            "matched_card_ids": [],
            "correct_answer": row["correct_answer"],
            "stats": dict(stats) if stats else {"score": 0, "streak": 0, "max_streak": 0},
            "points_awarded": row["points_awarded"],
            "multiplier": row["multiplier"],
            "suspicious": bool(row["is_suspicious"]),
            "new_badges": [],
        }

def get_daily_answer_count(telegram_id: int, day: str):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM quiz_answers_log
            WHERE telegram_id = ? AND category = 'daily' AND DATE(created_at) = ?
            """,
            (telegram_id, day),
        ).fetchone()
        return int(row["total"] or 0)

def get_daily_leaderboard(day: str, limit: int = 10):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            WITH first_daily_answers AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY telegram_id
                           ORDER BY created_at ASC, id ASC
                       ) AS daily_number
                FROM quiz_answers_log
                WHERE category = 'daily' AND DATE(created_at) = ?
            ),
            totals AS (
                SELECT telegram_id, username,
                       COALESCE(SUM(points_awarded), 0) AS score,
                       COALESCE(SUM(is_correct), 0) AS correct_answers
                FROM first_daily_answers
                WHERE daily_number <= ?
                GROUP BY telegram_id, username
            )
            SELECT telegram_id, username, score, correct_answers,
                   RANK() OVER (ORDER BY score DESC, correct_answers DESC, telegram_id ASC) AS rank
            FROM totals
            ORDER BY score DESC, correct_answers DESC, telegram_id ASC
            LIMIT ?
            """,
            (day, game_logic.DAILY_QUESTION_COUNT, limit),
        ).fetchall()
        return [dict(row) for row in rows]

def get_weekly_leaderboard(start_day: str, end_day: str, limit: int = 10):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT telegram_id, username,
                   COALESCE(SUM(points_awarded), 0) AS score,
                   COALESCE(SUM(is_correct), 0) AS correct_answers,
                   RANK() OVER (ORDER BY COALESCE(SUM(points_awarded), 0) DESC,
                                         COALESCE(SUM(is_correct), 0) DESC,
                                         telegram_id ASC) AS rank
            FROM quiz_answers_log
            WHERE DATE(created_at) BETWEEN ? AND ?
              AND is_suspicious = 0
            GROUP BY telegram_id, username
            ORDER BY score DESC, correct_answers DESC, telegram_id ASC
            LIMIT ?
            """,
            (start_day, end_day, limit),
        ).fetchall()
        return [dict(row) for row in rows]

def get_daily_summary(telegram_id: int, day: str, limit: int):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            WITH first_daily_answers AS (
                SELECT *,
                       ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS daily_number
                FROM quiz_answers_log
                WHERE telegram_id = ?
                  AND category = 'daily'
                  AND DATE(created_at) = ?
            )
            SELECT *
            FROM first_daily_answers
            WHERE daily_number <= ?
            ORDER BY daily_number ASC
            """,
            (telegram_id, day, limit),
        ).fetchall()
        answers = [dict(row) for row in rows]
        total = len(answers)
        correct = sum(1 for row in answers if row["is_correct"])
        points = sum(int(row["points_awarded"] or 0) for row in answers)
        correct_times = [int(row["response_ms"]) for row in answers if row["is_correct"] and row["response_ms"]]
        missed = [
            {
                "country_name": row["country_name"] or row["correct_answer"],
                "correct_answer": row["correct_answer"],
                "selected_answer": row["selected_answer"],
                "continent": row["continent"],
            }
            for row in answers
            if not row["is_correct"] and not row["is_suspicious"]
        ]
        rank = get_daily_user_rank(telegram_id, day, limit)
        return {
            "total": total,
            "correct": correct,
            "missed": len(missed),
            "points": points,
            "accuracy": round((correct / total) * 100) if total else 0,
            "avg_correct_ms": round(sum(correct_times) / len(correct_times)) if correct_times else None,
            "rank": rank,
            "hardest_missed": missed[:5],
        }

def get_daily_user_rank(telegram_id: int, day: str, limit: int):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            WITH first_daily_answers AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY telegram_id
                           ORDER BY created_at ASC, id ASC
                       ) AS daily_number
                FROM quiz_answers_log
                WHERE category = 'daily' AND DATE(created_at) = ?
            ),
            totals AS (
                SELECT telegram_id, username,
                       COALESCE(SUM(points_awarded), 0) AS score,
                       COALESCE(SUM(is_correct), 0) AS correct_answers
                FROM first_daily_answers
                WHERE daily_number <= ?
                GROUP BY telegram_id, username
            ),
            ranked AS (
                SELECT telegram_id, score, correct_answers,
                       RANK() OVER (ORDER BY score DESC, correct_answers DESC, telegram_id ASC) AS rank
                FROM totals
            )
            SELECT rank, score, correct_answers
            FROM ranked
            WHERE telegram_id = ?
            """,
            (day, limit, telegram_id),
        ).fetchone()
        return dict(row) if row else None

def get_recent_missed_flags(telegram_id: int, limit: int = 8):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT country_name,
                   continent,
                   correct_answer,
                   COUNT(*) AS misses,
                   MAX(created_at) AS last_missed_at
            FROM quiz_answers_log
            WHERE telegram_id = ?
              AND is_correct = 0
              AND is_suspicious = 0
              AND country_name IS NOT NULL
            GROUP BY country_name, continent, correct_answer
            ORDER BY misses DESC, last_missed_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

def evaluate_badges(telegram_id: int):
    unlocked = []
    with get_db_connection() as conn:
        existing = {
            row["badge_id"]
            for row in conn.execute(
                "SELECT badge_id FROM user_badges WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchall()
        }
        stats = conn.execute(
            "SELECT max_streak FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if stats and int(stats["max_streak"]) >= 5:
            maybe_unlock_badge(conn, telegram_id, "streak_starter", existing, unlocked)

        speed = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM (
                SELECT id
                FROM quiz_answers_log
                WHERE telegram_id = ?
                  AND is_correct = 1
                  AND response_ms < 2000
                  AND is_suspicious = 0
                ORDER BY id DESC
                LIMIT 5
            )
            """,
            (telegram_id,),
        ).fetchone()
        if speed and int(speed["total"]) >= 5:
            maybe_unlock_badge(conn, telegram_id, "speed_demon", existing, unlocked)

        europe = conn.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(is_correct), 0) AS correct
            FROM quiz_answers_log
            WHERE telegram_id = ?
              AND continent = 'Europe'
              AND category != 'capitals'
              AND is_suspicious = 0
            """,
            (telegram_id,),
        ).fetchone()
        total = int(europe["total"] or 0) if europe else 0
        correct = int(europe["correct"] or 0) if europe else 0
        if total >= 50 and correct / total >= 0.95:
            maybe_unlock_badge(conn, telegram_id, "europe_master", existing, unlocked)
        conn.commit()
    return unlocked

def maybe_unlock_badge(conn, telegram_id: int, badge_id: str, existing: set[str], unlocked: list[dict]):
    if badge_id in existing:
        return
    conn.execute(
        "INSERT OR IGNORE INTO user_badges (telegram_id, badge_id) VALUES (?, ?)",
        (telegram_id, badge_id),
    )
    row = conn.execute(
        "SELECT badge_id, name, description, icon FROM badges WHERE badge_id = ?",
        (badge_id,),
    ).fetchone()
    if row:
        unlocked.append(dict(row))
    existing.add(badge_id)

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

def complete_duel_session(duel_id: str, creator_score: int, opponent_score: int):
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE duel_sessions
            SET status = 'completed',
                creator_score = ?,
                opponent_score = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE duel_id = ?
            """,
            (creator_score, opponent_score, duel_id),
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

def quick_match(telegram_id: int, duel_id: str, match_format: str = "bo5"):
    with get_db_connection() as conn:
        active = find_active_duel_for_user(conn, telegram_id)
        if active:
            return {"duel_id": active["duel_id"], "status": active["status"], "format": match_format}
        opponent = conn.execute(
            """
            SELECT telegram_id, format
            FROM matchmaking_queue
            WHERE telegram_id != ? AND format = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (telegram_id, match_format),
        ).fetchone()
        if opponent:
            opponent_id = int(opponent["telegram_id"])
            conn.execute(
                "DELETE FROM matchmaking_queue WHERE telegram_id IN (?, ?)",
                (telegram_id, opponent_id),
            )
            conn.execute(
                """
                INSERT INTO duel_sessions (duel_id, creator_id, opponent_id, status, started_at)
                VALUES (?, ?, ?, 'ready', CURRENT_TIMESTAMP)
                """,
                (duel_id, opponent_id, telegram_id),
            )
            conn.commit()
            return {"duel_id": duel_id, "status": "ready", "opponent_id": opponent_id, "format": match_format}

        conn.execute(
            """
            INSERT INTO matchmaking_queue (telegram_id, format)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                format = excluded.format,
                created_at = CURRENT_TIMESTAMP
            """,
            (telegram_id, match_format),
        )
        conn.commit()
        return {"duel_id": None, "status": "waiting", "format": match_format}

def get_active_duel_for_user(telegram_id: int):
    with get_db_connection() as conn:
        active = find_active_duel_for_user(conn, telegram_id)
        return dict(active) if active else None

def find_active_duel_for_user(conn, telegram_id: int):
    return conn.execute(
        """
        SELECT *
        FROM duel_sessions
        WHERE status IN ('ready', 'active')
          AND (creator_id = ? OR opponent_id = ?)
        ORDER BY COALESCE(started_at, created_at) DESC
        LIMIT 1
        """,
        (telegram_id, telegram_id),
    ).fetchone()

def cancel_quick_match(telegram_id: int):
    with get_db_connection() as conn:
        conn.execute("DELETE FROM matchmaking_queue WHERE telegram_id = ?", (telegram_id,))
        conn.commit()

def get_duel_events(duel_id: str):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, duel_id, telegram_id, event_type, payload_json, created_at
            FROM duel_events
            WHERE duel_id = ?
            ORDER BY id ASC
            """,
            (duel_id,),
        ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            events.append(item)
        return events
