import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "flasgs_bot.sqlite3"

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
    
def update_score(telegram_id: int, is_correct: bool):
    stats = get_user_stats(telegram_id)
    if not stats:
        return
    
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