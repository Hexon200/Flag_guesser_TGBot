import hashlib
import random
from dataclasses import dataclass
from datetime import date
from typing import Any

from countries import COUNTRIES


@dataclass(frozen=True)
class DifficultyTier:
    key: str
    label: str
    choices: int
    timer_seconds: int
    base_points: int


DIFFICULTY_TIERS = {
    "easy": DifficultyTier("easy", "Easy", 4, 20, 10),
    "medium": DifficultyTier("medium", "Medium", 6, 15, 15),
    "hard": DifficultyTier("hard", "Hard", 10, 9, 25),
}

WELL_KNOWN_COUNTRIES = {
    "Argentina",
    "Australia",
    "Brazil",
    "Canada",
    "China",
    "France",
    "Germany",
    "India",
    "Italy",
    "Japan",
    "Mexico",
    "Russia",
    "Spain",
    "United Kingdom",
    "United States",
}

SIMILAR_FLAG_GROUPS = [
    {"Romania", "Chad", "Moldova", "Andorra"},
    {"Indonesia", "Monaco", "Poland"},
    {"Ireland", "Ivory Coast", "Italy"},
    {"Netherlands", "Luxembourg", "Russia", "Slovenia", "Slovakia"},
    {"Australia", "New Zealand", "Fiji", "Tuvalu"},
    {"Norway", "Iceland", "Denmark", "Sweden", "Finland"},
]

CONTINENTS = sorted({country["continent"] for country in COUNTRIES})

CATEGORY_PACKS = [
    {"key": "all", "label": "World flags", "kind": "flags"},
    {"key": "similar", "label": "Similar flags", "kind": "flags"},
    {"key": "capitals", "label": "Capital cities", "kind": "capitals"},
    {"key": "daily", "label": "Daily challenge", "kind": "daily"},
    *[
        {"key": f"continent:{continent}", "label": continent, "kind": "flags"}
        for continent in CONTINENTS
    ],
]

DAILY_QUESTION_COUNT = 12
MIN_PLAUSIBLE_RESPONSE_MS = 150

BADGE_RULES = [
    {
        "badge_id": "speed_demon",
        "name": "Speed Demon",
        "icon": "bolt",
        "description": "Answer 5 questions under 2 seconds in one session.",
    },
    {
        "badge_id": "streak_starter",
        "name": "Streak Starter",
        "icon": "flame",
        "description": "Reach a streak of 5.",
    },
    {
        "badge_id": "europe_master",
        "name": "Continent Master: Europe",
        "icon": "medal",
        "description": "Hold 95%+ accuracy across 50+ European flag questions.",
    },
]


def tier_for(key: str | None) -> DifficultyTier:
    return DIFFICULTY_TIERS.get((key or "medium").lower(), DIFFICULTY_TIERS["medium"])


def category_for(key: str | None) -> dict[str, str]:
    normalized = (key or "all").strip()
    return next((pack for pack in CATEGORY_PACKS if pack["key"] == normalized), CATEGORY_PACKS[0])


def countries_for_category(category_key: str) -> list[dict[str, Any]]:
    if category_key.startswith("continent:"):
        continent = category_key.split(":", 1)[1]
        return [country for country in COUNTRIES if country["continent"] == continent]
    if category_key == "similar":
        names = set().union(*SIMILAR_FLAG_GROUPS)
        return [country for country in COUNTRIES if country["name"] in names]
    return list(COUNTRIES)


def countries_for_tier(countries: list[dict[str, Any]], tier: DifficultyTier) -> list[dict[str, Any]]:
    if tier.key != "easy":
        return countries
    easy = [country for country in countries if country["name"] in WELL_KNOWN_COUNTRIES]
    return easy if len(easy) >= tier.choices else countries


def choose_country(
    telegram_id: int,
    category_key: str,
    tier: DifficultyTier,
    performance: dict[str, dict[str, int]],
) -> dict[str, Any]:
    pool = countries_for_tier(countries_for_category(category_key), tier)
    if not pool:
        pool = list(COUNTRIES)

    if not performance:
        return random.choice(pool)

    weights = []
    for country in pool:
        stats = performance.get(country["name"], {})
        attempts = int(stats.get("attempts", 0))
        misses = int(stats.get("misses", 0))
        if attempts < 3:
            weight = 1.2
        else:
            miss_rate = misses / max(attempts, 1)
            weight = 1 + (miss_rate * 5) + min(misses, 10) * 0.18
        weights.append(weight)
    return random.choices(pool, weights=weights, k=1)[0]


def make_wrong_choices(
    correct: dict[str, Any],
    category_key: str,
    choices_count: int,
) -> list[dict[str, Any]]:
    pool = countries_for_category(category_key)
    if category_key == "similar":
        group = next((group for group in SIMILAR_FLAG_GROUPS if correct["name"] in group), None)
        if group:
            pool = [country for country in COUNTRIES if country["name"] in group]
    wrong_pool = [country for country in pool if country["name"] != correct["name"]]
    if len(wrong_pool) < choices_count - 1:
        wrong_pool = [country for country in COUNTRIES if country["name"] != correct["name"]]
    return random.sample(wrong_pool, min(choices_count - 1, len(wrong_pool)))


def daily_country_set(day: date | None = None) -> list[dict[str, Any]]:
    day = day or date.today()
    seed = int(hashlib.sha256(day.isoformat().encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed)
    countries = list(COUNTRIES)
    rng.shuffle(countries)
    return countries[:DAILY_QUESTION_COUNT]


def daily_country_for_index(index: int, day: date | None = None) -> dict[str, Any]:
    countries = daily_country_set(day)
    return countries[index % len(countries)]


def score_for_answer(is_correct: bool, previous_streak: int, tier: DifficultyTier) -> dict[str, Any]:
    if not is_correct:
        return {"points": 0, "multiplier": 1.0, "next_streak": 0}
    next_streak = previous_streak + 1
    if next_streak >= 10:
        multiplier = 2.0
    elif next_streak >= 5:
        multiplier = 1.5
    elif next_streak >= 3:
        multiplier = 1.25
    else:
        multiplier = 1.0
    return {
        "points": round(tier.base_points * multiplier),
        "multiplier": multiplier,
        "next_streak": next_streak,
    }


def answer_is_suspicious(response_ms: int | None) -> bool:
    return response_ms is not None and response_ms < MIN_PLAUSIBLE_RESPONSE_MS


def badge_seed_rows() -> list[tuple[str, str, str, str, str]]:
    return [
        (
            badge["badge_id"],
            badge["name"],
            badge["description"],
            badge["badge_id"],
            badge["icon"],
        )
        for badge in BADGE_RULES
    ]
