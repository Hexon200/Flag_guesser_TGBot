# bot.py
import logging
import os
import sys
import random
import html
import uuid
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Import our countries dataset and database functions
from countries import COUNTRIES
import database

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Fetch Telegram Bot Token from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def clear_quiz_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all active quiz session states and cancel pending timers."""
    context.user_data.pop("current_question_token", None)
    context.user_data.pop("remaining_countries", None)
    context.user_data.pop("correct_name", None)
    context.user_data.pop("correct_answer", None)
    context.user_data.pop("options", None)
    context.user_data.pop("is_processing", None)
    context.user_data.pop("wrong_attempts", None)
    context.user_data.pop("is_hard_mode", None)
    context.user_data.pop("quiz_type", None)
    
    # Challenge states too
    context.user_data.pop("active_challenge_id", None)
    context.user_data.pop("challenge_role", None)
    context.user_data.pop("challenge_score", None)
    context.user_data.pop("challenge_index", None)
    context.user_data.pop("challenge_total", None)


async def show_welcome_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message and introduce the game."""
    if isinstance(update_or_query, Update):
        user = update_or_query.effective_user
        message_target = update_or_query.message
    else:
        # It's a callback query
        user = update_or_query.from_user
        message_target = update_or_query.message
        
    database.ensure_user(user.id, user.username or user.first_name)
    
    welcome_text = (
        f"👋 Hello {html.escape(user.first_name)}!\n\n"
        "Welcome to the <b>Country Quiz Bot</b>! 🌍🚩\n\n"
        "<b>Rules:</b>\n"
        "• Guess flags or capital cities of countries.\n"
        "• Correct answers increase your score and streak! 🔥\n"
        "• A wrong answer resets your streak.\n"
        "• Questions will not repeat during a quiz session.\n"
        "• 5 wrong guesses will end the quiz and return you here!\n\n"
        "<b>Commands:</b>\n"
        "/quiz - Start the Flag Guessing Quiz! 🚩\n"
        "/capital - Start the Capital Guessing Quiz! 🏙️\n"
        "/challenge - Challenge a friend to a 10-flag quiz ⚔️\n"
        "/stop - Stop the current quiz and return here ⛔️\n"
        "/stats - Check your score and streak\n"
        "/leaderboard - See top players"
    )
    
    # Start buttons
    keyboard = [
        [InlineKeyboardButton("🎮 Guess the Flag! 🚩", callback_data="start_quiz")],
        [InlineKeyboardButton("🏙️ Guess the Capital! 🏙️", callback_data="start_capital")],
        [InlineKeyboardButton("⚔️ Challenge a Friend ⚔️", callback_data="start_challenge")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if message_target:
        await message_target.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await context.bot.send_message(chat_id=user.id, text=welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def show_difficulty_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE, game_mode: str) -> None:
    """Show difficulty selection menu."""
    keyboard = [
        [
            InlineKeyboardButton("🟢 Normal Mode", callback_data=f"diff:normal:{game_mode}"),
            InlineKeyboardButton("🔴 Hard Mode (5s Timer)", callback_data=f"diff:hard:{game_mode}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Select your difficulty level:"
    
    if isinstance(update_or_query, Update) and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update_or_query.edit_message_text(text, reply_markup=reply_markup)


async def select_difficulty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle difficulty selection and proceed to continent selection."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    level = parts[1]
    game_mode = parts[2]
    
    context.user_data["is_hard_mode"] = (level == "hard")
    
    if game_mode == "flag":
        await show_continent_menu(query, context)
    else:
        await show_capital_continent_menu(query, context)


async def schedule_timeout(chat_id: int, message_id: int, token: int, user_data_ref: dict, context, bot) -> None:
    """Wait for 5 seconds and trigger timeout if the user hasn't answered yet."""
    await asyncio.sleep(5.0)
    
    if user_data_ref.get("current_question_token") == token:
        if user_data_ref.get("is_processing"):
            return
        user_data_ref["is_processing"] = True
        
        correct_name = user_data_ref.get("correct_name")
        correct_answer = user_data_ref.get("correct_answer")
        quiz_type = user_data_ref.get("quiz_type", "flag")
        
        safe_correct = html.escape(str(correct_answer))
        if quiz_type == "capital":
            result_text = f"⏰ <b>Timeout!</b> You took too long to answer.\nThe capital of {html.escape(correct_name)} was <b>{safe_correct}</b>.\nStreak reset to 0."
        else:
            result_text = f"⏰ <b>Timeout!</b> You took too long to answer.\nThe correct country was <b>{safe_correct}</b>.\nStreak reset to 0."
            
        try:
            database.update_score(chat_id, is_correct=False)
        except Exception as e:
            logger.error(f"Database error in timeout: {e}")
            
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=result_text,
                reply_markup=None,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to edit message caption on timeout: {e}")
            
        wrong_attempts = user_data_ref.get("wrong_attempts", 0) + 1
        user_data_ref["wrong_attempts"] = wrong_attempts
        
        if wrong_attempts >= 5:
            game_over_msg = "💀 <b>Game Over!</b> You have made 5 wrong guesses/timeouts. Returning to the main menu..."
            try:
                await bot.send_message(chat_id=chat_id, text=game_over_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send game over message: {e}")
                
            user_data_ref["remaining_countries"] = []
            user_data_ref["wrong_attempts"] = 0
            user_data_ref["is_processing"] = False
            
            welcome_text = (
                f"👋 Hello!\n\n"
                "Welcome to the <b>Country Quiz Bot</b>! 🌍🚩\n\n"
                "<b>Rules:</b>\n"
                "• Guess flags or capital cities of countries.\n"
                "• Correct answers increase your score and streak! 🔥\n"
                "• A wrong answer resets your streak.\n"
                "• Questions will not repeat during a quiz session.\n"
                "• 5 wrong guesses will end the quiz and return you here!\n\n"
                "<b>Commands:</b>\n"
                "/quiz - Start the Flag Guessing Quiz! 🚩\n"
                "/capital - Start the Capital Guessing Quiz! 🏙️\n"
                "/challenge - Challenge a friend to a 10-flag quiz ⚔️\n"
                "/stats - Check your score and streak\n"
                "/leaderboard - See top players"
            )
            keyboard = [
                [InlineKeyboardButton("🎮 Guess the Flag! 🚩", callback_data="start_quiz")],
                [InlineKeyboardButton("🏙️ Guess the Capital! 🏙️", callback_data="start_capital")],
                [InlineKeyboardButton("⚔️ Challenge a Friend ⚔️", callback_data="start_challenge")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            try:
                await bot.send_message(chat_id=chat_id, text=welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send welcome menu: {e}")
        else:
            await asyncio.sleep(1.5)
            
            class MockMessage:
                def __init__(self, chat_id, bot):
                    self.chat_id = chat_id
                    self.bot = bot
                async def reply_photo(self, photo, caption, reply_markup, **kwargs):
                    return await self.bot.send_photo(chat_id=self.chat_id, photo=photo, caption=caption, reply_markup=reply_markup, **kwargs)
                async def reply_text(self, text, **kwargs):
                    return await self.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)
            class MockQuery:
                def __init__(self, chat_id, bot):
                    self.message = MockMessage(chat_id, bot)
                    
            mock_query = MockQuery(chat_id, bot)
            if user_data_ref.get("current_question_token") == token:
                user_data_ref.pop("current_question_token", None)
                await send_new_flag(mock_query, context)


async def show_challenge_results(update_or_query, challenge) -> None:
    """Helper to display the outcome of a challenge."""
    creator_name = html.escape(challenge["creator_username"])
    creator_score = challenge["creator_score"]
    opponent_name = html.escape(challenge["opponent_username"] or "Friend")
    opponent_score = challenge["opponent_score"]
    total = len(challenge["countries_list"])
    quiz_type = challenge.get("quiz_type", "flag")
    mode_display = "Capitals" if quiz_type == "capital" else "Flags"
    
    if creator_score > opponent_score:
        result_str = f"🏆 <b>{creator_name}</b> wins!"
    elif opponent_score > creator_score:
        result_str = f"🏆 <b>{opponent_name}</b> wins!"
    else:
        result_str = "🤝 It's a <b>tie</b>!"
        
    message_text = (
        f"📊 <b>Challenge Results - {mode_display} ({challenge['continent']}):</b>\n\n"
        f"👤 {creator_name}: <b>{creator_score}/{total}</b>\n"
        f"👤 {opponent_name}: <b>{opponent_score}/{total}</b>\n\n"
        f"{result_str}"
    )
    
    if isinstance(update_or_query, Update) and update_or_query.message:
        await update_or_query.message.reply_text(message_text, parse_mode=ParseMode.HTML)
    elif hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(message_text, parse_mode=ParseMode.HTML)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message or join a multiplayer challenge."""
    user = update.effective_user
    database.ensure_user(user.id, user.username or user.first_name)
    
    # Check if there is a start argument for a challenge
    if context.args and context.args[0].startswith("challenge_"):
        clear_quiz_session(context)
        challenge_id = context.args[0].replace("challenge_", "")
        challenge = database.get_challenge(challenge_id)
        
        if not challenge:
            await update.message.reply_text("⚠️ Challenge not found! Starting normal menu.")
            await show_welcome_menu(update, context)
            return
            
        # Check if creator is playing their own challenge
        if challenge["creator_id"] == user.id:
            if challenge["creator_score"] is not None:
                await update.message.reply_text("🚫 You cannot play your own challenge! Share the link with a friend.")
                await show_welcome_menu(update, context)
                return
            else:
                await update.message.reply_text("🔄 Resuming your challenge!")
                context.user_data["active_challenge_id"] = challenge_id
                context.user_data["challenge_role"] = "creator"
                context.user_data["quiz_type"] = challenge.get("quiz_type", "flag")
                context.user_data["remaining_countries"] = challenge["countries_list"].copy()
                context.user_data["challenge_score"] = 0
                context.user_data["challenge_total"] = len(challenge["countries_list"])
                context.user_data["challenge_index"] = 0
                await send_new_flag(update, context)
                return
                
        # Check if opponent is already registered
        if challenge["opponent_id"] is not None and challenge["opponent_id"] != user.id:
            await update.message.reply_text("🚫 This challenge has already been completed or joined by someone else!")
            await show_welcome_menu(update, context)
            return
            
        # Check if they already completed it
        if challenge["opponent_id"] == user.id and challenge["opponent_score"] is not None:
            await update.message.reply_text("🏆 You have already completed this challenge! Here are the results:")
            await show_challenge_results(update, challenge)
            return
            
        # Join the challenge
        opponent_username = user.username or user.first_name
        database.join_challenge_opponent(challenge_id, user.id, opponent_username)
        
        # Start challenge for opponent
        context.user_data["active_challenge_id"] = challenge_id
        context.user_data["challenge_role"] = "opponent"
        context.user_data["quiz_type"] = challenge.get("quiz_type", "flag")
        context.user_data["remaining_countries"] = challenge["countries_list"].copy()
        context.user_data["challenge_score"] = 0
        context.user_data["challenge_total"] = len(challenge["countries_list"])
        context.user_data["challenge_index"] = 0
        
        creator_name = challenge["creator_username"]
        mode_display = "Capitals" if challenge.get("quiz_type") == "capital" else "Flags"
        await update.message.reply_text(
            f"⚔️ You accepted the <b>{mode_display}</b> challenge from <b>{html.escape(creator_name)}</b>!\n"
            f"Continent: <b>{challenge['continent']}</b>\n"
            f"Get ready to guess <b>{len(challenge['countries_list'])}</b> questions. Go!",
            parse_mode=ParseMode.HTML
        )
        await send_new_flag(update, context)
        return
        
    clear_quiz_session(context)
    await show_welcome_menu(update, context)


async def show_continent_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the menu to select a continent."""
    keyboard = [
        [
            InlineKeyboardButton("🌍 Africa", callback_data="continent:Africa"),
            InlineKeyboardButton("🌏 Asia", callback_data="continent:Asia")
        ],
        [
            InlineKeyboardButton("🇪🇺 Europe", callback_data="continent:Europe"),
            InlineKeyboardButton("🌎 North America", callback_data="continent:North America")
        ],
        [
            InlineKeyboardButton("🌎 South America", callback_data="continent:South America"),
            InlineKeyboardButton("🌏 Oceania", callback_data="continent:Oceania")
        ],
        [
            InlineKeyboardButton("🌐 All Continents", callback_data="continent:All")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Select a continent to start the flag guessing quiz:"
    
    # Check if we should send a new message or edit the existing one
    if isinstance(update_or_query, Update) and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        # Edit text if called from a button callback
        await update_or_query.edit_message_text(text, reply_markup=reply_markup)


async def show_challenge_continent_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the menu to select a continent for a challenge."""
    keyboard = [
        [
            InlineKeyboardButton("🌍 Africa", callback_data="chal_continent:Africa"),
            InlineKeyboardButton("🌏 Asia", callback_data="chal_continent:Asia")
        ],
        [
            InlineKeyboardButton("🇪🇺 Europe", callback_data="chal_continent:Europe"),
            InlineKeyboardButton("🌎 North America", callback_data="chal_continent:North America")
        ],
        [
            InlineKeyboardButton("🌎 South America", callback_data="chal_continent:South America"),
            InlineKeyboardButton("🌏 Oceania", callback_data="chal_continent:Oceania")
        ],
        [
            InlineKeyboardButton("🌐 All Continents", callback_data="chal_continent:All")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Select a continent for the 10-question challenge quiz:"
    
    if isinstance(update_or_query, Update) and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update_or_query.edit_message_text(text, reply_markup=reply_markup)


async def show_capital_continent_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the menu to select a continent for guessing capitals."""
    keyboard = [
        [
            InlineKeyboardButton("🌍 Africa", callback_data="cap_continent:Africa"),
            InlineKeyboardButton("🌏 Asia", callback_data="cap_continent:Asia")
        ],
        [
            InlineKeyboardButton("🇪🇺 Europe", callback_data="cap_continent:Europe"),
            InlineKeyboardButton("🌎 North America", callback_data="cap_continent:North America")
        ],
        [
            InlineKeyboardButton("🌎 South America", callback_data="cap_continent:South America"),
            InlineKeyboardButton("🌏 Oceania", callback_data="cap_continent:Oceania")
        ],
        [
            InlineKeyboardButton("🌐 All Continents", callback_data="cap_continent:All")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Select a continent to start the capital guessing quiz:"
    
    if isinstance(update_or_query, Update) and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update_or_query.edit_message_text(text, reply_markup=reply_markup)


async def select_capital_continent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Initialize the session's list of remaining capitals based on selected continent."""
    query = update.callback_query
    await query.answer()
    
    selected = query.data.split(":")[1]
    
    # Filter countries
    if selected == "All":
        filtered = COUNTRIES
        display_name = "All Continents"
    else:
        filtered = [c for c in COUNTRIES if c["continent"] == selected]
        display_name = selected
        
    context.user_data["quiz_type"] = "capital"
    context.user_data["remaining_countries"] = [c["name"] for c in filtered]
    context.user_data["selected_continent"] = display_name
    context.user_data["wrong_attempts"] = 0
    
    is_hard = context.user_data.get("is_hard_mode", False)
    mode_text = "🔴 Hard Mode (5s timer)" if is_hard else "🟢 Normal Mode"
    
    await query.delete_message()
    await query.message.reply_text(
        f"🏁 Starting Capital Guessing for <b>{display_name}</b> ({len(filtered)} countries)!\n"
        f"Difficulty: <b>{mode_text}</b>",
        parse_mode=ParseMode.HTML
    )
    
    await send_new_flag(query, context)


async def show_challenge_mode_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the menu to select a game mode for the challenge."""
    keyboard = [
        [
            InlineKeyboardButton("🎮 Guess the Flag! 🚩", callback_data="chal_mode:flag"),
            InlineKeyboardButton("🏙️ Guess the Capital! 🏙️", callback_data="chal_mode:capital")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Select the game mode for your 1v1 challenge quiz:"
    
    if isinstance(update_or_query, Update) and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update_or_query.edit_message_text(text, reply_markup=reply_markup)


async def select_challenge_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle challenge mode selection, store it, and proceed to continent selection."""
    query = update.callback_query
    await query.answer()
    
    chal_type = query.data.split(":")[1]
    context.user_data["temp_chal_type"] = chal_type
    
    await show_challenge_continent_menu(query, context)


async def select_challenge_continent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Initialize a challenge session and send the share link to the creator."""
    query = update.callback_query
    await query.answer()
    
    selected = query.data.split(":")[1]
    
    # Filter countries
    if selected == "All":
        filtered = COUNTRIES
        display_name = "All Continents"
    else:
        filtered = [c for c in COUNTRIES if c["continent"] == selected]
        display_name = selected
        
    count = min(10, len(filtered))
    if count == 0:
        await query.message.reply_text("No countries available for this continent.")
        return
        
    selected_countries = random.sample(filtered, count)
    selected_country_names = [c["name"] for c in selected_countries]
    
    challenge_id = uuid.uuid4().hex[:8]
    user = query.from_user
    username = user.username or user.first_name
    
    quiz_type = context.user_data.pop("temp_chal_type", "flag")
    
    try:
        database.create_challenge(
            challenge_id=challenge_id,
            creator_id=user.id,
            creator_username=username,
            continent=display_name,
            countries_list=selected_country_names,
            quiz_type=quiz_type
        )
    except Exception as e:
        logger.error(f"Database error creating challenge: {e}")
        await query.message.reply_text("⚠️ Failed to create challenge. Please try again.")
        return
        
    bot_username = (await context.bot.get_me()).username
    share_link = f"https://t.me/{bot_username}?start=challenge_{challenge_id}"
    
    mode_display = "Capitals" if quiz_type == "capital" else "Flags"
    msg = (
        f"🏁 <b>1v1 Challenge Created!</b>\n"
        f"Mode: <b>{mode_display}</b>\n"
        f"Continent: <b>{display_name}</b>\n\n"
        f"Share this link with your friend to play together:\n"
        f"<code>{share_link}</code>\n\n"
        f"Click the button below when you are ready to start your quiz!"
    )
    
    keyboard = [
        [InlineKeyboardButton("🎮 Start Challenge Quiz", callback_data=f"play_challenge:{challenge_id}")],
        [InlineKeyboardButton("📢 Share Link", url=f"https://t.me/share/url?url={share_link}&text=Let's%20play%20a%20Flag%20Guessing%20Challenge!")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.delete_message()
    await query.message.reply_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def play_challenge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the challenge quiz for the creator when they click the start button."""
    query = update.callback_query
    await query.answer()
    
    challenge_id = query.data.split(":")[1]
    challenge = database.get_challenge(challenge_id)
    
    if not challenge:
        await query.message.reply_text("⚠️ Challenge not found.")
        return
        
    # Set up session user_data for creator
    context.user_data["active_challenge_id"] = challenge_id
    context.user_data["challenge_role"] = "creator"
    context.user_data["quiz_type"] = challenge.get("quiz_type", "flag")
    context.user_data["remaining_countries"] = challenge["countries_list"].copy()
    context.user_data["challenge_score"] = 0
    context.user_data["challenge_total"] = len(challenge["countries_list"])
    context.user_data["challenge_index"] = 0
    
    await query.delete_message()
    await send_new_flag(query, context)


async def complete_challenge(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle completing the 10 questions in a challenge."""
    challenge_id = context.user_data.get("active_challenge_id")
    role = context.user_data.get("challenge_role")
    score = context.user_data.get("challenge_score", 0)
    
    # Clean up session
    context.user_data.pop("active_challenge_id", None)
    context.user_data.pop("challenge_role", None)
    context.user_data.pop("challenge_score", None)
    context.user_data.pop("challenge_index", None)
    context.user_data.pop("challenge_total", None)
    context.user_data.pop("remaining_countries", None)
    context.user_data.pop("is_processing", None)
    
    if not challenge_id or not role:
        return
        
    if isinstance(update_or_query, Update) and update_or_query.message:
        message_target = update_or_query.message
    else:
        message_target = update_or_query.message
        
    if not message_target:
        user = update_or_query.from_user if not isinstance(update_or_query, Update) else update_or_query.effective_user
        message_target = context.bot
        send_method = lambda text, **kwargs: context.bot.send_message(chat_id=user.id, text=text, **kwargs)
    else:
        send_method = lambda text, **kwargs: message_target.reply_text(text, **kwargs)
        
    # Save the score
    if role == "creator":
        database.update_challenge_creator_score(challenge_id, score)
    else:
        database.update_challenge_opponent_score(challenge_id, score)
        
    # Retrieve updated challenge details
    challenge = database.get_challenge(challenge_id)
    total = len(challenge["countries_list"])
    
    creator_score = challenge["creator_score"]
    opponent_score = challenge["opponent_score"]
    
    creator_id = challenge["creator_id"]
    creator_name = challenge["creator_username"]
    opponent_id = challenge["opponent_id"]
    opponent_name = challenge["opponent_username"] or "Friend"
    
    # Check if both have completed their turn
    if creator_score is not None and opponent_score is not None:
        if creator_score > opponent_score:
            winner_text = f"🏆 <b>{html.escape(creator_name)}</b> wins!"
        elif opponent_score > creator_score:
            winner_text = f"🏆 <b>{html.escape(opponent_name)}</b> wins!"
        else:
            winner_text = "🤝 It's a <b>tie</b>!"
            
        mode_display = "Capitals" if challenge.get("quiz_type") == "capital" else "Flags"
        results_msg = (
            f"📊 <b>Challenge Results - {mode_display} ({challenge['continent']}):</b>\n\n"
            f"👤 {html.escape(creator_name)}: <b>{creator_score}/{total}</b>\n"
            f"👤 {html.escape(opponent_name)}: <b>{opponent_score}/{total}</b>\n\n"
            f"{winner_text}"
        )
        
        await send_method(results_msg, parse_mode=ParseMode.HTML)
        
        # Send results to the other player
        other_chat_id = opponent_id if role == "creator" else creator_id
        try:
            await context.bot.send_message(chat_id=other_chat_id, text=results_msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to notify other player of results: {e}")
            
    else:
        # Waiting for the other player
        await send_method(
            f"🏁 <b>Quiz Completed!</b>\n\n"
            f"You scored: <b>{score}/{total}</b>\n"
            f"Waiting for your opponent to complete their quiz!",
            parse_mode=ParseMode.HTML
        )
        
        if role == "opponent":
            mode_display = "Capitals" if challenge.get("quiz_type") == "capital" else "Flags"
            creator_notify_msg = (
                f"🔔 <b>Challenge Update!</b>\n\n"
                f"Your friend <b>{html.escape(opponent_name)}</b> completed your {mode_display.lower()} challenge ({challenge['continent']}) and scored <b>{opponent_score}/{total}</b>!\n"
                f"Use `/start challenge_{challenge_id}` or check your challenge message to play your quiz."
            )
            try:
                await context.bot.send_message(chat_id=creator_id, text=creator_notify_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to notify creator of opponent score: {e}")
            
        await show_welcome_menu(update_or_query, context)


async def select_continent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Initialize the session's list of remaining flags based on selected continent."""
    query = update.callback_query
    await query.answer()
    
    selected = query.data.split(":")[1]
    
    # Filter countries
    if selected == "All":
        filtered = COUNTRIES
        display_name = "All Continents"
    else:
        filtered = [c for c in COUNTRIES if c["continent"] == selected]
        display_name = selected
        
    # Store the remaining country names in session
    context.user_data["quiz_type"] = "flag"
    context.user_data["remaining_countries"] = [c["name"] for c in filtered]
    context.user_data["selected_continent"] = display_name
    context.user_data["wrong_attempts"] = 0
    
    is_hard = context.user_data.get("is_hard_mode", False)
    mode_text = "🔴 Hard Mode (5s timer)" if is_hard else "🟢 Normal Mode"
    
    # Notify the user and start the quiz
    await query.delete_message()
    await query.message.reply_text(
        f"🏁 Starting quiz for <b>{display_name}</b> ({len(filtered)} flags)!\n"
        f"Difficulty: <b>{mode_text}</b>",
        parse_mode=ParseMode.HTML
    )
    
    # Send the first flag!
    await send_new_flag(query, context)


async def send_new_flag(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pick a random country from the remaining list and send the flag photo."""
    remaining = context.user_data.get("remaining_countries")
    continent = context.user_data.get("selected_continent", "All")
    
    # If the list is empty or doesn't exist, they have finished all flags!
    if not remaining:
        if context.user_data.get("active_challenge_id"):
            await complete_challenge(update_or_query, context)
            return
            
        quiz_type = context.user_data.get("quiz_type", "flag")
        if quiz_type == "capital":
            msg = f"🏆 <b>Congratulations!</b> You successfully guessed all capitals in <b>{html.escape(continent)}</b>!"
            await update_or_query.message.reply_text(msg, parse_mode=ParseMode.HTML)
            await show_capital_continent_menu(update_or_query, context)
        else:
            msg = f"🏆 <b>Congratulations!</b> You have successfully guessed all flags in <b>{html.escape(continent)}</b>!"
            await update_or_query.message.reply_text(msg, parse_mode=ParseMode.HTML)
            await show_continent_menu(update_or_query, context)
        return
        
    # 1. Pick a random country from the remaining list
    correct_name = random.choice(remaining)
    
    # Get the country dictionary from our COUNTRIES database
    correct_country = next(c for c in COUNTRIES if c["name"] == correct_name)
    
    # Remove it so it doesn't repeat
    remaining.remove(correct_name)
    context.user_data["remaining_countries"] = remaining
    
    # 2. Pick 3 wrong options from the full list
    wrong_countries = random.sample([c for c in COUNTRIES if c["name"] != correct_name], 3)
    
    # 3. Combine and shuffle options
    options = [correct_country] + wrong_countries
    random.shuffle(options)
    
    quiz_type = context.user_data.get("quiz_type", "flag")
    if quiz_type == "capital":
        correct_answer = correct_country["capital"]
        option_labels = [c["capital"] for c in options]
    else:
        correct_answer = correct_country["name"]
        option_labels = [c["name"] for c in options]
        
    # Store session answers
    context.user_data["correct_name"] = correct_name
    context.user_data["correct_answer"] = correct_answer
    context.user_data["options"] = option_labels
    context.user_data["is_processing"] = False
    
    # Create the inline keyboard buttons (2x2 grid)
    keyboard = [
        [
            InlineKeyboardButton(option_labels[0], callback_data="guess:0"),
            InlineKeyboardButton(option_labels[1], callback_data="guess:1")
        ],
        [
            InlineKeyboardButton(option_labels[2], callback_data="guess:2"),
            InlineKeyboardButton(option_labels[3], callback_data="guess:3")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send the flag image
    if context.user_data.get("active_challenge_id"):
        challenge_index = context.user_data.get("challenge_index", 0) + 1
        challenge_total = context.user_data.get("challenge_total", 10)
        caption = f"What country does this flag belong to?\n(Challenge Question: {challenge_index}/{challenge_total})"
    elif quiz_type == "capital":
        caption = f"What is the capital of <b>{html.escape(correct_name)}</b>?\n(Remaining: {len(remaining) + 1} questions)"
    else:
        caption = f"What country does this flag belong to?\n(Remaining: {len(remaining) + 1} flags)"
    
    msg = await update_or_query.message.reply_photo(
        photo=correct_country["flag_url"],
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    is_hard = context.user_data.get("is_hard_mode", False)
    # Background timer runs only for solo quizzes (not challenges) in Hard Mode
    if is_hard and not context.user_data.get("active_challenge_id"):
        token = random.randint(1, 1000000)
        context.user_data["current_question_token"] = token
        
        chat_id = msg.chat_id
        message_id = msg.message_id
        
        asyncio.create_task(schedule_timeout(chat_id, message_id, token, context.user_data, context, context.bot))


async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /quiz command."""
    user = update.effective_user
    database.ensure_user(user.id, user.username or user.first_name)
    clear_quiz_session(context)
    await show_difficulty_menu(update, context, game_mode="flag")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /stop command to manually abort the current quiz."""
    clear_quiz_session(context)
    await update.message.reply_text("⛔️ Quiz stopped. Returning to the main menu...")
    await show_welcome_menu(update, context)


async def handle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check the user's guess, update database, and trigger next flag."""
    query = update.callback_query
    await query.answer()
    
    # Cancel any pending timeout background task immediately
    context.user_data.pop("current_question_token", None)
    
    # Prevent double-click race conditions
    if context.user_data.get("is_processing"):
        return
    context.user_data["is_processing"] = True
    
    correct_name = context.user_data.get("correct_name")
    correct_answer = context.user_data.get("correct_answer")
    options = context.user_data.get("options")
    
    if not correct_name or not correct_answer or not options:
        await query.message.reply_text("Session expired. Start a new game with /quiz!")
        context.user_data["is_processing"] = False
        return
        
    guessed_index = int(query.data.split(":")[1])
    guessed_name = options[guessed_index]
    is_correct = (guessed_name == correct_answer)
    
    is_challenge = context.user_data.get("active_challenge_id") is not None
    quiz_type = context.user_data.get("quiz_type", "flag")
    
    if is_challenge:
        challenge_score = context.user_data.get("challenge_score", 0)
        if is_correct:
            challenge_score += 1
            context.user_data["challenge_score"] = challenge_score
        
        challenge_index = context.user_data.get("challenge_index", 0) + 1
        context.user_data["challenge_index"] = challenge_index
    else:
        wrong_attempts = context.user_data.get("wrong_attempts", 0)
        if not is_correct:
            wrong_attempts += 1
            context.user_data["wrong_attempts"] = wrong_attempts
        
    try:
        user_id = query.from_user.id
        new_stats = database.update_score(user_id, is_correct)
    except Exception as e:
        logger.error(f"Database error in handle_guess: {e}")
        await query.message.reply_text("⚠️ There was an issue saving your score. Please try again.")
        context.user_data["is_processing"] = False
        return
    
    safe_correct = html.escape(str(correct_answer))
    if is_challenge:
        result_text = (
            f"{'✅ <b>Correct!</b>' if is_correct else '❌ <b>Incorrect.</b>'} It is {safe_correct}.\n"
            f"Challenge Score: <b>{context.user_data['challenge_score']}/{context.user_data['challenge_index']}</b>"
        )
    elif quiz_type == "capital":
        if is_correct:
            result_text = f"✅ <b>Correct!</b> The capital of {html.escape(correct_name)} is <b>{safe_correct}</b>.\n🔥 Current Streak: <b>{new_stats['streak']}</b>"
        else:
            result_text = f"❌ <b>Incorrect.</b> The capital of {html.escape(correct_name)} was <b>{safe_correct}</b>.\nStreak reset to 0."
    else:
        if is_correct:
            result_text = f"✅ <b>Correct!</b> It is {safe_correct}.\n🔥 Current Streak: <b>{new_stats['streak']}</b>"
        else:
            result_text = (
                f"❌ <b>Incorrect.</b> The correct country was <b>{safe_correct}</b>.\n"
                f"Streak reset to 0.\n"
                f"Wrong guesses: <b>{wrong_attempts}/5</b>"
            )
        
    await query.edit_message_caption(caption=result_text, reply_markup=None, parse_mode=ParseMode.HTML)
    
    if not is_challenge and not is_correct and wrong_attempts >= 5:
        # Game over - send notification and return to main menu
        game_over_msg = "💀 <b>Game Over!</b> You have made 5 wrong guesses. Returning to the main menu..."
        await query.message.reply_text(game_over_msg, parse_mode=ParseMode.HTML)
        
        # Clear session
        context.user_data["remaining_countries"] = []
        context.user_data["wrong_attempts"] = 0
        context.user_data["is_processing"] = False
        
        # Show main/welcome menu
        await show_welcome_menu(query, context)
        return
        
    # Send next flag automatically
    await send_new_flag(query, context)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's score and streak details."""
    user = update.effective_user
    database.ensure_user(user.id, user.username or user.first_name)
    
    try:
        stats = database.get_user_stats(user.id)
        stats_text = (
            f"📊 <b>Your Stats ({html.escape(user.first_name)}):</b>\n\n"
            f"🏆 Total Score: <b>{stats['score']}</b> points\n"
            f"🔥 Current Streak: <b>{stats['streak']}</b>\n"
            f"⚡ Longest Streak: <b>{stats['max_streak']}</b>"
        )
        await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Database error in stats: {e}")
        await update.message.reply_text("⚠️ Failed to retrieve stats. Please try again.")


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the top players."""
    try:
        rows = database.get_leaderboard()
        if not rows:
            await update.message.reply_text("No scores recorded yet. Be the first with /quiz!")
            return
            
        text = "🏆 <b>Leaderboard:</b>\n\n"
        for idx, row in enumerate(rows, 1):
            username = html.escape(row['username'] or "Player")
            text += f"{idx}. <b>{username}</b> — {row['score']} pts (Max Streak: {row['max_streak']})\n"
            
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Database error in leaderboard: {e}")
        await update.message.reply_text("⚠️ Failed to load leaderboard. Please try again.")


async def start_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle start_quiz button click from welcome menu."""
    query = update.callback_query
    await query.answer()
    await show_difficulty_menu(query, context, game_mode="flag")


async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /challenge command."""
    user = update.effective_user
    database.ensure_user(user.id, user.username or user.first_name)
    clear_quiz_session(context)
    await show_challenge_mode_menu(update, context)


async def start_challenge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle start_challenge button click from welcome menu."""
    query = update.callback_query
    await query.answer()
    await show_challenge_mode_menu(query, context)


async def capital_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /capital command."""
    user = update.effective_user
    database.ensure_user(user.id, user.username or user.first_name)
    clear_quiz_session(context)
    await show_difficulty_menu(update, context, game_mode="capital")


async def start_capital_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle start_capital button click from welcome menu."""
    query = update.callback_query
    await query.answer()
    await show_difficulty_menu(query, context, game_mode="capital")


async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle non-command text inputs by guiding users to commands."""
    await update.message.reply_text("Please use buttons or commands like /quiz, /capital, /challenge, /stats, or /leaderboard to play!")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN environment variable not set.", file=sys.stderr)
        sys.exit(1)
        
    # Initialize the database tables
    database.init_db()
    
    # Configure longer connection timeouts for serverless/cloud environments
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(request).build()
    
    # Register command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz_command))
    app.add_handler(CommandHandler("challenge", challenge_command))
    app.add_handler(CommandHandler("capital", capital_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    
    # Register callback query handlers (for button clicks)
    app.add_handler(CallbackQueryHandler(start_quiz_callback, pattern="^start_quiz$"))
    app.add_handler(CallbackQueryHandler(start_capital_callback, pattern="^start_capital$"))
    app.add_handler(CallbackQueryHandler(start_challenge_callback, pattern="^start_challenge$"))
    app.add_handler(CallbackQueryHandler(select_difficulty_callback, pattern="^diff:"))
    app.add_handler(CallbackQueryHandler(select_challenge_mode_callback, pattern="^chal_mode:"))
    app.add_handler(CallbackQueryHandler(select_continent, pattern="^continent:"))
    app.add_handler(CallbackQueryHandler(select_capital_continent, pattern="^cap_continent:"))
    app.add_handler(CallbackQueryHandler(select_challenge_continent, pattern="^chal_continent:"))
    app.add_handler(CallbackQueryHandler(play_challenge_callback, pattern="^play_challenge:"))
    app.add_handler(CallbackQueryHandler(handle_guess, pattern="^guess:"))
    
    # Register fallback message handler
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, fallback_message))
    
    logger.info("Flag guessing bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=5)


if __name__ == "__main__":
    main()