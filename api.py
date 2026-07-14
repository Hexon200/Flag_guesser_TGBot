import base64
import hashlib
import hmac
import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
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
DUEL_CONNECTIONS: dict[str, set[WebSocket]] = {}


class TelegramAuthRequest(BaseModel):
    init_data: str


class AnswerRequest(BaseModel):
    question_id: str
    choice_id: str | None = None
    card_ids: list[str] | None = None


class ChallengeCreateRequest(BaseModel):
    mode: str = "duel"


class ChallengeJoinRequest(BaseModel):
    duel_id: str


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
        choices_count: int = Query(6, ge=4, le=6),
    ):
        check_rate_limit(int(current_user["id"]), "question", limit=30, window_seconds=60)
        if mode == "match":
            return create_match_deck(int(current_user["id"]))
        return create_grid_question(int(current_user["id"]), choices_count)

    @app.post("/api/quiz/answer")
    async def answer_question(payload: AnswerRequest, current_user: Annotated[dict, Depends(require_user)]):
        telegram_id = int(current_user["id"])
        check_rate_limit(telegram_id, "answer", limit=45, window_seconds=60)
        session = database.get_question_session(payload.question_id, telegram_id)
        if not session:
            raise HTTPException(status_code=404, detail="Question not found")
        if session["answered_at"] and session["mode"] == "grid":
            raise HTTPException(status_code=409, detail="Question already answered")

        if session["mode"] == "match":
            result = resolve_match_answer(session, payload)
        else:
            result = resolve_grid_answer(session, payload)

        username = current_user.get("username") or current_user.get("first_name") or "Player"
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
        )
        new_stats = database.update_score(telegram_id, result["correct"], username)

        if session["mode"] == "grid" or result.get("completed"):
            database.mark_question_answered(payload.question_id, telegram_id)

        return {
            "correct": result["correct"],
            "completed": result.get("completed", False),
            "matched_card_ids": result.get("matched_card_ids", []),
            "correct_answer": result["correct_answer"],
            "stats": new_stats,
        }

    @app.get("/api/profile/stats")
    async def profile_stats(current_user: Annotated[dict, Depends(require_user)]):
        return database.get_profile_stats(int(current_user["id"]))

    @app.get("/api/leaderboard")
    async def leaderboard(current_user: Annotated[dict, Depends(require_user)], scope: str = "global"):
        if scope != "global":
            raise HTTPException(status_code=400, detail="Only global leaderboard is available in phase 1")
        telegram_id = int(current_user["id"])
        return {
            "leaders": database.get_leaderboard_with_ranks(10),
            "you": database.get_user_rank_window(telegram_id),
        }

    @app.post("/api/challenge/create")
    async def create_challenge(current_user: Annotated[dict, Depends(require_user)], payload: ChallengeCreateRequest):
        telegram_id = int(current_user["id"])
        check_rate_limit(telegram_id, "challenge", limit=8, window_seconds=60)
        duel_id = uuid.uuid4().hex[:12]
        database.create_duel_session(duel_id, telegram_id)
        return {"duel_id": duel_id, "mode": payload.mode, "status": "waiting"}

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
        return {"duel_id": payload.duel_id, "status": "ready"}

    @app.websocket("/ws/duel/{duel_id}")
    async def duel_socket(websocket: WebSocket, duel_id: str):
        await websocket.accept()
        DUEL_CONNECTIONS.setdefault(duel_id, set()).add(websocket)
        try:
            await broadcast_duel(duel_id, {"type": "presence", "players": len(DUEL_CONNECTIONS[duel_id])})
            while True:
                message = await websocket.receive_json()
                await broadcast_duel(duel_id, {"type": "event", "payload": message})
        except WebSocketDisconnect:
            pass
        finally:
            DUEL_CONNECTIONS.get(duel_id, set()).discard(websocket)
            await broadcast_duel(duel_id, {"type": "presence", "players": len(DUEL_CONNECTIONS.get(duel_id, set()))})

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


def public_user(user: dict) -> dict:
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "photo_url": user.get("photo_url"),
    }


def create_grid_question(telegram_id: int, choices_count: int) -> dict:
    correct = random.choice(COUNTRIES)
    wrong = random.sample([country for country in COUNTRIES if country["name"] != correct["name"]], choices_count - 1)
    countries = [correct] + wrong
    random.shuffle(countries)
    choices = [make_choice(country) for country in countries]
    question_id = uuid.uuid4().hex
    database.create_question_session(
        question_id=question_id,
        telegram_id=telegram_id,
        mode="grid",
        prompt=correct["name"],
        correct_country=correct["name"],
        choices=choices,
    )
    return {
        "question_id": question_id,
        "mode": "grid",
        "prompt": {"type": "country_name", "text": correct["name"]},
        "choices": [{"id": choice["id"], "flag_url": choice["flag_url"]} for choice in choices],
    }


def create_match_deck(telegram_id: int) -> dict:
    selected = random.sample(COUNTRIES, 4)
    cards = []
    stored_choices = []
    for country in selected:
        text_card = {
            "id": uuid.uuid4().hex,
            "kind": "name",
            "country": country["name"],
            "label": country["name"],
        }
        flag_card = {
            "id": uuid.uuid4().hex,
            "kind": "flag",
            "country": country["name"],
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
    )
    return {"question_id": question_id, "mode": "match", "cards": cards}


def resolve_grid_answer(session: dict, payload: AnswerRequest) -> dict:
    if not payload.choice_id:
        raise HTTPException(status_code=400, detail="choice_id is required")
    selected = next((choice for choice in session["choices"] if choice["id"] == payload.choice_id), None)
    if not selected:
        raise HTTPException(status_code=400, detail="Choice not found")
    correct = selected["country"] == session["correct_country"]
    return {
        "correct": correct,
        "selected_answer": selected["country"],
        "correct_answer": session["correct_country"],
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
        "response_ms": response_ms(session),
    }


def make_choice(country: dict) -> dict:
    return {"id": uuid.uuid4().hex, "country": country["name"], "flag_url": flag_url(country)}


def client_card(card: dict) -> dict:
    if card["kind"] == "flag":
        return {"id": card["id"], "kind": "flag", "flag_url": card["flag_url"]}
    return {"id": card["id"], "kind": "name", "label": card["label"]}


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


async def broadcast_duel(duel_id: str, payload: dict):
    sockets = list(DUEL_CONNECTIONS.get(duel_id, set()))
    for socket in sockets:
        try:
            await socket.send_json(payload)
        except RuntimeError:
            DUEL_CONNECTIONS.get(duel_id, set()).discard(socket)


app = create_app()
