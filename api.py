import asyncio
import base64
import hashlib
import hmac
import json
import os
import random
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
import game_logic
from countries import COUNTRIES

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
JWT_SECRET = os.getenv("TMA_JWT_SECRET") or BOT_TOKEN or "local-dev-secret"
JWT_ALGORITHM = "HS256"
SESSION_HOURS = int(os.getenv("TMA_SESSION_HOURS", "6"))
INIT_DATA_MAX_AGE_SECONDS = int(os.getenv("TMA_INIT_DATA_MAX_AGE_SECONDS", "86400"))
DEV_AUTH_ENABLED = os.getenv("TMA_DEV_AUTH", "").lower() in {"1", "true", "yes"}

BASE_DIR = Path(__file__).parent
TMA_DIR = BASE_DIR / "tma"

RATE_BUCKETS: dict[tuple[int, str], list[float]] = {}
DUEL_CONNECTIONS: dict[str, dict[int, set[WebSocket]]] = {}
DUEL_ROOMS: dict[str, dict] = {}


class TelegramAuthRequest(BaseModel):
    init_data: str


class AnswerRequest(BaseModel):
    question_id: str
    choice_id: str | None = None
    card_ids: list[str] | None = None
    idempotency_key: str | None = None


class ChallengeCreateRequest(BaseModel):
    mode: str = "duel"
    format: str = "bo5"


class ChallengeJoinRequest(BaseModel):
    duel_id: str


class QuickMatchRequest(BaseModel):
    format: str = "bo5"


def create_app() -> FastAPI:
    database.init_db()
    app = FastAPI(title="Flag Guessing TMA API")
    app.mount("/tma", StaticFiles(directory=TMA_DIR, html=True), name="tma")

    @app.get("/")
    async def root():
        return FileResponse(TMA_DIR / "index.html")

    @app.post("/api/auth/telegram")
    async def auth_telegram(payload: TelegramAuthRequest):
        user = validate_telegram_init_data(payload.init_data)
        username = user.get("username") or user.get("first_name") or "Player"
        telegram_id = int(user["id"])
        database.ensure_user(telegram_id, username)
        token = issue_session_token(user)
        return {"token": token, "user": public_user(user)}

    @app.get("/api/quiz/question")
    async def get_question(
        current_user: Annotated[dict, Depends(require_user)],
        mode: str = Query("grid", pattern="^(grid|match)$"),
        choices_count: int | None = Query(None, ge=4, le=10),
        difficulty: str = Query("medium", pattern="^(easy|medium|hard)$"),
        category: str = Query("all"),
    ):
        check_rate_limit(int(current_user["id"]), "question", limit=30, window_seconds=60)
        selected_tier = game_logic.tier_for(difficulty)
        selected_category = game_logic.category_for(category)
        if mode == "match":
            return create_match_deck(int(current_user["id"]), selected_category["key"], selected_tier)
        count = choices_count or selected_tier.choices
        return create_grid_question(int(current_user["id"]), count, selected_category["key"], selected_tier.key)

    @app.get("/api/game/options")
    async def game_options(current_user: Annotated[dict, Depends(require_user)]):
        return {
            "difficulties": [
                {
                    "key": tier.key,
                    "label": tier.label,
                    "choices": tier.choices,
                    "timer_seconds": tier.timer_seconds,
                    "base_points": tier.base_points,
                }
                for tier in game_logic.DIFFICULTY_TIERS.values()
            ],
            "categories": game_logic.CATEGORY_PACKS,
            "daily_total": game_logic.DAILY_QUESTION_COUNT,
        }

    @app.post("/api/quiz/answer")
    async def answer_question(payload: AnswerRequest, current_user: Annotated[dict, Depends(require_user)]):
        telegram_id = int(current_user["id"])
        check_rate_limit(telegram_id, "answer", limit=45, window_seconds=60)
        receipt_key = payload.idempotency_key or default_idempotency_key(payload)
        receipt = database.get_answer_receipt(receipt_key, telegram_id)
        if receipt:
            return receipt

        session = database.get_question_session(payload.question_id, telegram_id)
        if not session:
            raise HTTPException(status_code=404, detail="Question not found")
        if session["answered_at"] and session["mode"] == "grid":
            previous = database.get_previous_grid_response(payload.question_id, telegram_id)
            if previous:
                return previous
            raise HTTPException(status_code=409, detail="Question already answered")

        if session["mode"] == "match":
            result = resolve_match_answer(session, payload)
        else:
            result = resolve_grid_answer(session, payload)

        username = current_user.get("username") or current_user.get("first_name") or "Player"
        tier = game_logic.tier_for(session.get("difficulty"))
        stats_before = database.get_user_stats(telegram_id)
        previous_streak = int(stats_before["streak"] or 0) if stats_before else 0
        suspicious = game_logic.answer_is_suspicious(result["response_ms"])
        scoring = game_logic.score_for_answer(result["correct"] and not suspicious, previous_streak, tier)
        new_stats = database.apply_score(
            telegram_id=telegram_id,
            is_correct=result["correct"] and not suspicious,
            points=scoring["points"],
            next_streak=scoring["next_streak"],
            username=username,
        )

        database.log_quiz_answer(
            telegram_id=telegram_id,
            username=username,
            mode=session["mode"],
            question_id=payload.question_id,
            prompt=session["prompt"] or "",
            selected_answer=result["selected_answer"],
            correct_answer=result["correct_answer"],
            is_correct=result["correct"],
            response_ms=result["response_ms"],
            country_name=result.get("country_name"),
            continent=result.get("continent"),
            category=session.get("category") or "all",
            difficulty=session.get("difficulty") or "medium",
            points_awarded=scoring["points"],
            streak_after=new_stats["streak"],
            multiplier=scoring["multiplier"],
            is_suspicious=suspicious,
        )

        if session["mode"] == "grid" or result.get("completed"):
            database.mark_question_answered(payload.question_id, telegram_id)

        new_badges = database.evaluate_badges(telegram_id)
        response = {
            "correct": result["correct"],
            "completed": result.get("completed", False),
            "matched_card_ids": result.get("matched_card_ids", []),
            "correct_answer": result["correct_answer"],
            "stats": new_stats,
            "points_awarded": scoring["points"],
            "multiplier": scoring["multiplier"],
            "suspicious": suspicious,
            "new_badges": new_badges,
        }
        database.save_answer_receipt(receipt_key, telegram_id, payload.question_id, response)
        return response

    @app.get("/api/profile/stats")
    async def profile_stats(current_user: Annotated[dict, Depends(require_user)]):
        return database.get_profile_stats(int(current_user["id"]))

    @app.get("/api/leaderboard")
    async def leaderboard(current_user: Annotated[dict, Depends(require_user)], scope: str = "global"):
        telegram_id = int(current_user["id"])
        if scope == "daily":
            return {
                "leaders": database.get_daily_leaderboard(date.today().isoformat(), 10),
                "you": [],
                "scope": "daily",
            }
        if scope != "global":
            raise HTTPException(status_code=400, detail="Unknown leaderboard scope")
        return {
            "leaders": database.get_leaderboard_with_ranks(10),
            "you": database.get_user_rank_window(telegram_id),
            "scope": "global",
        }

    @app.post("/api/challenge/create")
    async def create_challenge(current_user: Annotated[dict, Depends(require_user)], payload: ChallengeCreateRequest):
        telegram_id = int(current_user["id"])
        check_rate_limit(telegram_id, "challenge", limit=8, window_seconds=60)
        duel_id = uuid.uuid4().hex[:12]
        database.create_duel_session(duel_id, telegram_id)
        database.log_duel_event(duel_id, telegram_id, "created", {"format": payload.format, "mode": payload.mode})
        return {"duel_id": duel_id, "mode": payload.mode, "format": payload.format, "status": "waiting"}

    @app.post("/api/challenge/join")
    async def join_challenge(current_user: Annotated[dict, Depends(require_user)], payload: ChallengeJoinRequest):
        telegram_id = int(current_user["id"])
        check_rate_limit(telegram_id, "challenge", limit=8, window_seconds=60)
        duel = database.get_duel_session(payload.duel_id)
        if not duel:
            raise HTTPException(status_code=404, detail="Duel not found")
        if duel["creator_id"] == telegram_id:
            raise HTTPException(status_code=400, detail="Creator cannot join as opponent")
        database.join_duel_session(payload.duel_id, telegram_id)
        database.log_duel_event(payload.duel_id, telegram_id, "joined", {})
        return {"duel_id": payload.duel_id, "status": "ready"}

    @app.post("/api/matchmaking/quick-match")
    async def quick_match(current_user: Annotated[dict, Depends(require_user)], payload: QuickMatchRequest):
        telegram_id = int(current_user["id"])
        check_rate_limit(telegram_id, "matchmaking", limit=10, window_seconds=60)
        match_format = payload.format if payload.format in {"bo5", "bo10"} else "bo5"
        duel_id = uuid.uuid4().hex[:12]
        result = database.quick_match(telegram_id, duel_id, match_format)
        if result["duel_id"]:
            database.log_duel_event(result["duel_id"], telegram_id, "quick_match_ready", {"format": match_format})
        return result

    @app.delete("/api/matchmaking/quick-match")
    async def cancel_quick_match(current_user: Annotated[dict, Depends(require_user)]):
        telegram_id = int(current_user["id"])
        database.cancel_quick_match(telegram_id)
        return {"status": "cancelled"}

    @app.get("/api/matchmaking/status")
    async def matchmaking_status(current_user: Annotated[dict, Depends(require_user)]):
        telegram_id = int(current_user["id"])
        duel = database.get_active_duel_for_user(telegram_id)
        if duel:
            return {"status": duel["status"], "duel_id": duel["duel_id"]}
        return {"status": "waiting"}

    @app.get("/api/duel/{duel_id}/replay")
    async def duel_replay(duel_id: str, current_user: Annotated[dict, Depends(require_user)]):
        duel = database.get_duel_session(duel_id)
        if not duel:
            raise HTTPException(status_code=404, detail="Duel not found")
        return {"duel": duel, "events": database.get_duel_events(duel_id)}

    @app.websocket("/ws/duel/{duel_id}")
    async def duel_socket(websocket: WebSocket, duel_id: str, token: str | None = Query(None)):
        user = user_from_ws_token(token)
        telegram_id = int(user["id"])
        await websocket.accept()
        DUEL_CONNECTIONS.setdefault(duel_id, {}).setdefault(telegram_id, set()).add(websocket)
        try:
            await broadcast_duel_presence(duel_id)
            while True:
                message = await websocket.receive_json()
                await handle_duel_message(duel_id, telegram_id, message)
        except WebSocketDisconnect:
            pass
        finally:
            user_sockets = DUEL_CONNECTIONS.get(duel_id, {}).get(telegram_id, set())
            user_sockets.discard(websocket)
            if not user_sockets and duel_id in DUEL_CONNECTIONS:
                DUEL_CONNECTIONS[duel_id].pop(telegram_id, None)
            await broadcast_duel_presence(duel_id)

    return app


def validate_telegram_init_data(init_data: str) -> dict:
    if DEV_AUTH_ENABLED and not init_data:
        return {"id": int(os.getenv("TMA_DEV_TELEGRAM_ID", "1000")), "first_name": "Dev Player"}
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN is required")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing Telegram auth hash")

    auth_date = int(values.get("auth_date", "0") or "0")
    if not auth_date or time.time() - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="Telegram auth data expired")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram auth signature")

    try:
        user = json.loads(values["user"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Missing Telegram user data") from exc
    return user


def issue_session_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "id": int(user["id"]),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "photo_url": user.get("photo_url"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=SESSION_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def require_user(authorization: Annotated[str | None, Header()] = None) -> dict:
    if DEV_AUTH_ENABLED and not authorization:
        return {"id": int(os.getenv("TMA_DEV_TELEGRAM_ID", "1000")), "first_name": "Dev Player"}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc


def user_from_ws_token(token: str | None) -> dict:
    if DEV_AUTH_ENABLED and not token:
        return {"id": int(os.getenv("TMA_DEV_TELEGRAM_ID", "1000")), "first_name": "Dev Player"}
    if not token:
        raise HTTPException(status_code=401, detail="Missing WebSocket token")
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid WebSocket token") from exc


def public_user(user: dict) -> dict:
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "photo_url": user.get("photo_url"),
    }


def create_grid_question(telegram_id: int, choices_count: int, category_key: str = "all", difficulty: str = "medium") -> dict:
    tier = game_logic.tier_for(difficulty)
    if category_key == "daily":
        answered = database.get_daily_answer_count(telegram_id, date.today().isoformat())
        correct = game_logic.daily_country_for_index(answered)
        daily_progress = {"answered": min(answered, game_logic.DAILY_QUESTION_COUNT), "total": game_logic.DAILY_QUESTION_COUNT}
    else:
        performance = database.get_user_country_performance(telegram_id)
        correct = game_logic.choose_country(telegram_id, category_key, tier, performance)
        daily_progress = None
    question_kind = "capital" if category_key == "capitals" else "country"
    wrong = game_logic.make_wrong_choices(correct, category_key, min(choices_count, tier.choices))
    countries = [correct] + wrong
    random.shuffle(countries)
    choices = [make_choice(country, question_kind) for country in countries]
    if question_kind == "capital":
        for choice in choices:
            choice["expected_answer"] = correct["capital"]
            choice["correct_continent"] = correct["continent"]
    question_id = uuid.uuid4().hex
    prompt_text = correct["name"] if question_kind == "country" else "Name the capital"
    database.create_question_session(
        question_id=question_id,
        telegram_id=telegram_id,
        mode="grid",
        prompt=prompt_text,
        correct_country=correct["name"],
        choices=choices,
        category=category_key,
        difficulty=tier.key,
        question_kind=question_kind,
        timer_seconds=tier.timer_seconds,
    )
    question = {
        "question_id": question_id,
        "mode": "grid",
        "difficulty": tier.key,
        "category": category_key,
        "timer_seconds": tier.timer_seconds,
        "prompt": prompt_for_country(correct, question_kind),
        "choices": [client_choice(choice, question_kind) for choice in choices],
    }
    if daily_progress:
        question["daily_progress"] = daily_progress
    return question


def create_match_deck(telegram_id: int, category_key: str = "all", tier: game_logic.DifficultyTier | None = None) -> dict:
    tier = tier or game_logic.DIFFICULTY_TIERS["medium"]
    pool = game_logic.countries_for_tier(game_logic.countries_for_category(category_key), tier)
    if len(pool) < 4:
        pool = list(COUNTRIES)
    selected = random.sample(pool, 4)
    cards = []
    stored_choices = []
    for country in selected:
        text_card = {
            "id": uuid.uuid4().hex,
            "kind": "name",
            "country": country["name"],
            "continent": country["continent"],
            "label": country["name"],
        }
        flag_card = {
            "id": uuid.uuid4().hex,
            "kind": "flag",
            "country": country["name"],
            "continent": country["continent"],
            "flag_url": flag_url(country),
        }
        cards.extend([client_card(text_card), client_card(flag_card)])
        stored_choices.extend([text_card, flag_card])
    random.shuffle(cards)
    question_id = uuid.uuid4().hex
    database.create_question_session(
        question_id=question_id,
        telegram_id=telegram_id,
        mode="match",
        prompt="Match countries to flags",
        correct_country="",
        choices=stored_choices,
        category=category_key,
        difficulty=tier.key,
        question_kind="match",
        timer_seconds=0,
    )
    return {"question_id": question_id, "mode": "match", "category": category_key, "difficulty": tier.key, "cards": cards}


def resolve_grid_answer(session: dict, payload: AnswerRequest) -> dict:
    if not payload.choice_id:
        raise HTTPException(status_code=400, detail="choice_id is required")
    selected = next((choice for choice in session["choices"] if choice["id"] == payload.choice_id), None)
    if not selected:
        raise HTTPException(status_code=400, detail="Choice not found")
    question_kind = session.get("question_kind") or "country"
    correct_answer = selected.get("expected_answer") or session["correct_country"]
    correct = selected["answer"] == correct_answer
    return {
        "correct": correct,
        "selected_answer": selected["answer"],
        "correct_answer": correct_answer,
        "country_name": session["correct_country"],
        "continent": selected.get("correct_continent") or selected.get("continent"),
        "question_kind": question_kind,
        "response_ms": response_ms(session),
    }


def resolve_match_answer(session: dict, payload: AnswerRequest) -> dict:
    if not payload.card_ids or len(payload.card_ids) != 2:
        raise HTTPException(status_code=400, detail="Two card_ids are required")
    selected = [choice for choice in session["choices"] if choice["id"] in payload.card_ids]
    if len(selected) != 2:
        raise HTTPException(status_code=400, detail="Cards not found")
    kinds = {choice["kind"] for choice in selected}
    correct = len(kinds) == 2 and selected[0]["country"] == selected[1]["country"]
    matched_ids = payload.card_ids if correct else []
    return {
        "correct": correct,
        "completed": False,
        "matched_card_ids": matched_ids,
        "selected_answer": " + ".join(choice["country"] for choice in selected),
        "correct_answer": selected[0]["country"] if correct else "matching country and flag",
        "country_name": selected[0]["country"] if correct else None,
        "continent": selected[0].get("continent"),
        "response_ms": response_ms(session),
    }


def make_choice(country: dict, question_kind: str = "country") -> dict:
    if question_kind == "capital":
        return {
            "id": uuid.uuid4().hex,
            "country": country["name"],
            "answer": country["capital"],
            "expected_answer": country["capital"],
            "label": country["capital"],
            "flag_url": flag_url(country),
            "continent": country["continent"],
            "correct_continent": country["continent"],
        }
    return {
        "id": uuid.uuid4().hex,
        "country": country["name"],
        "answer": country["name"],
        "label": country["name"],
        "flag_url": flag_url(country),
        "continent": country["continent"],
    }


def prompt_for_country(country: dict, question_kind: str) -> dict:
    if question_kind == "capital":
        return {
            "type": "flag_to_capital",
            "text": "Which capital belongs to this flag?",
            "flag_url": flag_url(country),
            "country": country["name"],
        }
    return {"type": "country_name", "text": country["name"]}


def client_choice(choice: dict, question_kind: str) -> dict:
    if question_kind == "capital":
        return {"id": choice["id"], "label": choice["label"]}
    return {"id": choice["id"], "flag_url": choice["flag_url"]}


def client_card(card: dict) -> dict:
    if card["kind"] == "flag":
        return {"id": card["id"], "kind": "flag", "flag_url": card["flag_url"]}
    return {"id": card["id"], "kind": "name", "label": card["label"]}


def default_idempotency_key(payload: AnswerRequest) -> str:
    if payload.choice_id:
        return f"{payload.question_id}:choice:{payload.choice_id}"
    if payload.card_ids:
        return f"{payload.question_id}:cards:{':'.join(sorted(payload.card_ids))}"
    return f"{payload.question_id}:empty"


def flag_url(country: dict) -> str:
    return country["flag_url"]


def response_ms(session: dict) -> int | None:
    raw_created = session.get("created_at")
    if not raw_created:
        return None
    try:
        created = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - created).total_seconds() * 1000))


def check_rate_limit(telegram_id: int, bucket: str, limit: int, window_seconds: int):
    now = time.time()
    key = (telegram_id, bucket)
    entries = [stamp for stamp in RATE_BUCKETS.get(key, []) if now - stamp < window_seconds]
    if len(entries) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    entries.append(now)
    RATE_BUCKETS[key] = entries


async def handle_duel_message(duel_id: str, telegram_id: int, message: dict):
    message_type = message.get("type")
    if message_type == "ready":
        await send_duel_state(duel_id)
        return
    if message_type == "start":
        await start_duel(duel_id, telegram_id, message.get("format", "bo5"))
        return
    if message_type == "answer":
        await answer_duel_question(duel_id, telegram_id, message.get("choice_id"))
        return
    await broadcast_duel(duel_id, {"type": "event", "payload": message})


async def start_duel(duel_id: str, telegram_id: int, match_format: str):
    players = list(DUEL_CONNECTIONS.get(duel_id, {}).keys())
    if len(players) < 2:
        await send_to_duel_user(duel_id, telegram_id, {"type": "duel_waiting", "message": "Waiting for another player."})
        return
    total = 10 if match_format == "bo10" else 5
    room = {
        "started": True,
        "round": 0,
        "total": total,
        "players": players[:2],
        "scores": {str(player_id): 0 for player_id in players[:2]},
        "answered": set(),
        "current": None,
    }
    DUEL_ROOMS[duel_id] = room
    database.log_duel_event(duel_id, telegram_id, "started", {"format": match_format, "players": players[:2]})
    await next_duel_round(duel_id)


async def send_duel_state(duel_id: str):
    await broadcast_duel_presence(duel_id)
    room = DUEL_ROOMS.get(duel_id)
    if room and room.get("current"):
        await broadcast_duel(duel_id, public_duel_question(room))


async def next_duel_round(duel_id: str):
    room = DUEL_ROOMS.get(duel_id)
    if not room:
        return
    room["round"] += 1
    room["answered"] = set()
    correct = random.choice(COUNTRIES)
    wrong = random.sample([country for country in COUNTRIES if country["name"] != correct["name"]], 5)
    countries = [correct] + wrong
    random.shuffle(countries)
    choices = [{"id": uuid.uuid4().hex, "country": country["name"], "flag_url": flag_url(country)} for country in countries]
    room["current"] = {
        "prompt": correct["name"],
        "correct_country": correct["name"],
        "choices": choices,
    }
    database.log_duel_event(duel_id, 0, "round_started", {"round": room["round"], "correct_country": correct["name"]})
    await broadcast_duel(duel_id, public_duel_question(room))


async def answer_duel_question(duel_id: str, telegram_id: int, choice_id: str | None):
    room = DUEL_ROOMS.get(duel_id)
    if not room or not room.get("current"):
        await send_to_duel_user(duel_id, telegram_id, {"type": "duel_waiting", "message": "Start the duel first."})
        return
    if telegram_id in room["answered"]:
        return
    selected = next((choice for choice in room["current"]["choices"] if choice["id"] == choice_id), None)
    is_correct = bool(selected and selected["country"] == room["current"]["correct_country"])
    if is_correct:
        room["scores"][str(telegram_id)] = int(room["scores"].get(str(telegram_id), 0)) + 1
    room["answered"].add(telegram_id)
    database.log_duel_event(
        duel_id,
        telegram_id,
        "answered",
        {
            "round": room["round"],
            "selected_country": selected["country"] if selected else None,
            "correct": is_correct,
            "score": room["scores"].get(str(telegram_id), 0),
        },
    )
    await send_to_duel_user(
        duel_id,
        telegram_id,
        {
            "type": "duel_answer_result",
            "user_id": telegram_id,
            "correct": is_correct,
            "correct_answer": room["current"]["correct_country"],
            "scores": room["scores"],
        },
    )
    await broadcast_duel(
        duel_id,
        {
            "type": "duel_peer_answered",
            "user_id": telegram_id,
            "answered_count": len(room["answered"]),
            "player_count": len(room["players"]),
        },
    )
    if len(room["answered"]) >= len(room["players"]):
        await broadcast_duel(
            duel_id,
            {
                "type": "duel_round_result",
                "round": room["round"],
                "correct_answer": room["current"]["correct_country"],
                "scores": room["scores"],
            },
        )
        await asyncio.sleep(1.1)
        if room["round"] >= room["total"]:
            await complete_duel(duel_id)
        else:
            await next_duel_round(duel_id)


async def complete_duel(duel_id: str):
    room = DUEL_ROOMS.get(duel_id)
    if not room:
        return
    scores = room["scores"]
    winner_id = None
    if len(scores) >= 2:
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        if ordered[0][1] != ordered[1][1]:
            winner_id = int(ordered[0][0])
    database.log_duel_event(duel_id, winner_id or 0, "completed", {"scores": scores, "winner_id": winner_id})
    await broadcast_duel(duel_id, {"type": "duel_complete", "scores": scores, "winner_id": winner_id})
    DUEL_ROOMS.pop(duel_id, None)


def public_duel_question(room: dict) -> dict:
    current = room["current"]
    return {
        "type": "duel_question",
        "round": room["round"],
        "total": room["total"],
        "prompt": {"type": "country_name", "text": current["prompt"]},
        "choices": [{"id": choice["id"], "flag_url": choice["flag_url"]} for choice in current["choices"]],
        "scores": room["scores"],
    }


async def broadcast_duel_presence(duel_id: str):
    players = list(DUEL_CONNECTIONS.get(duel_id, {}).keys())
    await broadcast_duel(duel_id, {"type": "presence", "players": len(players), "player_ids": players})


async def send_to_duel_user(duel_id: str, telegram_id: int, payload: dict):
    sockets = list(DUEL_CONNECTIONS.get(duel_id, {}).get(telegram_id, set()))
    for socket in sockets:
        try:
            await socket.send_json(payload)
        except RuntimeError:
            DUEL_CONNECTIONS.get(duel_id, {}).get(telegram_id, set()).discard(socket)


async def broadcast_duel(duel_id: str, payload: dict):
    sockets = [
        socket
        for user_sockets in DUEL_CONNECTIONS.get(duel_id, {}).values()
        for socket in user_sockets
    ]
    for socket in sockets:
        try:
            await socket.send_json(payload)
        except RuntimeError:
            for user_sockets in DUEL_CONNECTIONS.get(duel_id, {}).values():
                user_sockets.discard(socket)


app = create_app()
