# bot.py
# Author: Opswill
# Feature: Universal Dual-Language Translation Bot (Supports bi-directional translation for any two languages + dual translation display for other languages)
# Key Features: Whitelist | Detailed Audit Logging | Translation Statistics | Inline Icon Beautification | High Concurrency Optimization

import os
import json
import asyncio
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import typing as t
import uuid
from contextvars import ContextVar
from functools import wraps
from loguru import logger


from telegram import Update, Chat
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)
from google.cloud import translate_v3 as translate
from loguru import logger


# ==================================== Configuration and Constants ====================================

CONFIG_FILE = "config.json"
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Load configuration file
if not Path(CONFIG_FILE).exists():
    raise FileNotFoundError(f"Error: Configuration file {CONFIG_FILE} not found!")

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)

BOT_TOKEN = config["bot_token"]
# Ensure Google Application Credentials path is set in the environment
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google_credentials"]
PROJECT_ID = config["project_id"]

# ============ Configurable Dual-Language Pair ============
LANG_A_CODE = config["language_a"]["code"]       # E.g.: "zh-CN"
LANG_A_NAME = config["language_a"]["name"]       # E.g.: "Chinese"
LANG_A_FLAG = config["language_a"]["flag"]       # E.g.: "🇨🇳"

LANG_B_CODE = config["language_b"]["code"]       # E.g.: "vi"
LANG_B_NAME = config["language_b"]["name"]       # E.g.: "Vietnamese"
LANG_B_FLAG = config["language_b"]["flag"]       # E.g.: "🇻🇳"

# Other configuration settings
MAX_MESSAGES_PER_MINUTE = config.get("max_messages_per_minute", 60)
REPLY_DELAY_SECONDS = config.get("reply_delay_seconds", 0.1)
CONFIDENCE_THRESHOLD = config.get("confidence_threshold", 0.7)
SHORT_TEXT_BYPASS_LIMIT = config.get("short_text_bypass_chars", 6)

# Whitelist settings
ALLOWED_CHAT_IDS: t.Set[int] = set(config.get("allowed_chat_ids", []))
ALLOWED_ADMIN_USERNAMES: t.Set[str] = {
    name.lower().lstrip("@") for name in config.get("allowed_admin_usernames", [])
}

# Google Cloud Translation API parent location
PARENT_LOCATION = f"projects/{PROJECT_ID}/locations/global"


# ==================================== Logging and Statistics System ====================================

# Context variable for request ID, used for tracing individual translation operations
request_id_var: ContextVar[str] = ContextVar("request_id", default="--------")

def get_request_id() -> str:
    """Retrieves the request ID for the current context."""
    rid = request_id_var.get()
    return rid if rid else "NO-REQ-ID"

# Main log configuration (rotated daily)
# Patch loguru to include the context-bound request_id in every log record
logger = logger.patch(lambda record: record["extra"].update(request_id=request_id_var.get()))
logger.remove()
logger.add(
    LOG_DIR / "bot_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="90 days",
    level=config.get("log_level", "INFO"),
    encoding="utf-8",
    backtrace=True,
    diagnose=True,
    # Custom format including the request_id for detailed tracing
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[request_id]} | {message}",
)

def with_request_id(handler):
    """
    Decorator to automatically inject a unique request_id into the log context
    for any Telegram handler, allowing for easy tracing of operations.
    """
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Generate a short UUID (8 chars is enough for log viewing)
        request_id = uuid.uuid4().hex[:8].upper()

        # Bind the request_id to the current asynchronous task's context
        token = request_id_var.set(request_id)
        try:
            return await handler(update, context)
        finally:
            # Clean up the context variable regardless of success or exception
            request_id_var.reset(token)

    return wrapper

# Daily translation statistics file
stats_file = LOG_DIR / f"stats_{datetime.now():%Y-%m-%d}.json"

def load_daily_stats() -> defaultdict:
    """Loads today's translation statistics from the file, initializing if necessary."""
    if stats_file.exists():
        try:
            with open(stats_file, "r", encoding="utf-8") as f:
                return defaultdict(int, json.load(f))
        except Exception as e:
            logger.warning(f"Statistics file corrupted, re-initializing: {e}")
    return defaultdict(int)

def save_daily_stats(stats: defaultdict):
    """Saves the current daily statistics back to the file."""
    try:
        with open(stats_file, "w", encoding="utf-8") as f:
            # Convert defaultdict to dict for JSON serialization
            json.dump(dict(stats), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save statistics: {e}")


# ==================================== Rate Limiting System ====================================

message_timestamps = defaultdict(list)

def is_rate_limited(chat_id: int) -> bool:
    """Checks if the chat has exceeded the allowed MAX_MESSAGES_PER_MINUTE."""
    now = datetime.now()
    timestamps = message_timestamps[chat_id]
    
    # Remove records older than 60 seconds
    timestamps = [t for t in timestamps if (now - t).total_seconds() < 60]
    message_timestamps[chat_id] = timestamps

    if len(timestamps) >= MAX_MESSAGES_PER_MINUTE:
        logger.warning(f"Rate limit triggered -> Chat_ID: {chat_id} | Current frequency: {len(timestamps) + 1}/minute")
        return True
    
    # Record the new message timestamp
    timestamps.append(now)
    return False


# ==================================== Whitelist Check ====================================

def is_chat_authorized(chat_id: int, chat: Chat) -> bool:
    """Checks if the chat_id or the sender's username is in the whitelist."""
    if not ALLOWED_CHAT_IDS and not ALLOWED_ADMIN_USERNAMES:
        return True  # If no whitelist is configured, allow all

    display_name = (chat.title or chat.first_name or "Unknown User").strip()
    username = getattr(chat, "username", None)

    # Check against Chat ID whitelist
    if chat_id in ALLOWED_CHAT_IDS:
        logger.debug(f"Whitelist passed (Chat ID) -> {chat_id} | Chat: {display_name}")
        return True
    
    # Check against Admin Username whitelist (useful for private chats)
    if username and username.lower() in ALLOWED_ADMIN_USERNAMES:
        logger.debug(f"Admin Whitelist passed -> @{username} (Chat_ID: {chat_id})")
        return True

    # Message from an unauthorized source
    logger.info(f"Ignored message from unauthorized session -> Chat_ID: {chat_id} | Name: {display_name} | @{username or 'None'}")
    return False


# ==================================== Google Translate Client ====================================

def create_translation_client():
    """Initializes and returns the Google Cloud Translation client."""
    try:
        # Client will automatically use GOOGLE_APPLICATION_CREDENTIALS from environment
        client = translate.TranslationServiceClient()
        logger.success("Google Cloud Translation Client initialized successfully")
        return client
    except Exception as e:
        logger.critical(f"Google Translate Client initialization failed! Please check credentials path and permissions: {e}", exc_info=True)
        raise


# ==================================== Core Translation Functions ====================================

async def detect_source_language(text: str, client) -> t.Optional[t.Tuple[str, float]]:
    """Performs language detection using Google Cloud Translation API."""
    def _detect():
        try:
            response = client.detect_language(
                request={"parent": PARENT_LOCATION, "content": text, "mime_type": "text/plain"}
            )
            # The API returns the best detection result first
            lang = response.languages[0]
            # Normalize language code to a common format (e.g., zh-cn instead of zh-CN)
            return lang.language_code.lower().replace("_", "-"), lang.confidence
        except Exception as e:
            logger.error(f"Language detection failed: {e}")
            return None
    # Run the synchronous API call in a separate thread to avoid blocking the event loop
    return await asyncio.to_thread(_detect)

async def translate_text(text: str, target_lang: str, client) -> t.Optional[str]:
    """Performs translation using Google Cloud Translation API."""
    def _translate():
        try:
            response = client.translate_text(
                request={
                    "parent": PARENT_LOCATION,
                    "contents": [text],
                    "target_language_code": target_lang,
                    "mime_type": "text/plain",
                }
            )
            return response.translations[0].translated_text if response.translations else None
        except Exception as e:
            logger.warning(f"Translation failed [{target_lang}]: {e}")
            return None
    # Run the synchronous API call in a separate thread
    return await asyncio.to_thread(_translate)


# ==================================== /stats Command Handler ====================================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /stats command, displaying daily translation statistics."""
    user = update.effective_user
    # Check if the user is an authorized admin
    if not user or user.username and user.username.lower().lstrip("@") not in ALLOWED_ADMIN_USERNAMES:
        logger.info(f"Unauthorized user attempted to view stats -> UserID: {user.id} | @{user.username or 'No Username'}")
        return

    # Retrieve stats from bot_data, defaulting to an empty dict if not found
    stats = context.bot_data.get("daily_stats", defaultdict(int))
    today = datetime.now().strftime("%Y-%m-%d")

    await update.message.reply_text(
        f"Translation Bot Statistics · {today}\n\n"
        f"Total Translations: {stats['total']}\n"
        f"{LANG_A_NAME} -> {LANG_B_NAME}: {stats['a_to_b']}\n"
        f"{LANG_B_NAME} -> {LANG_A_NAME}: {stats['b_to_a']}\n"
        f"Other Lang -> Dual-Trans: {stats['other_to_dual']}\n"
        f"Partial Dual-Trans Success: {stats['partial_dual']}\n"
        f"Translation Failures: {stats['failed']}\n\n"
        f"Current Language Pair: {LANG_A_FLAG} {LANG_A_NAME} ↔ {LANG_B_FLAG} {LANG_B_NAME}",
        disable_notification=True
    )


# ==================================== Main Message Handling Logic ====================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main message handler for detecting language and performing translation."""
    msg = update.message
    if not msg:
        return

    # Get text to translate: prioritize caption, otherwise use text
    translation_source = "text" 
    text_to_translate = msg.text

    if msg.caption:
        text_to_translate = msg.caption
        translation_source = "caption"
    
    if not text_to_translate:
        return

    original_text = text_to_translate.strip()
    if not original_text:
        return

    # Ignore commands (already handled by CommandHandler or to prevent double processing)
    if original_text.startswith("/"):
        return

    chat = msg.chat
    chat_id = chat.id
    user = msg.from_user
    logger.debug(f"Message received -> Chat_ID: {chat_id} | User: @{user.username or user.id} | Content: \"{original_text}\" | Source: {translation_source.upper()}")
    
    # 1. Whitelist Check
    if not is_chat_authorized(chat_id, chat):
        return
    
    # 2. Rate Limit Check + Prevent self-reply (bot replying to itself)
    if is_rate_limited(chat_id) or user.id == context.bot.id:
        return
        
    client = context.bot_data["translate_client"]
    stats: defaultdict = context.bot_data["daily_stats"]
    
    # 3. Language Detection
    detection = await detect_source_language(original_text, client)
    if not detection:
        stats["failed"] += 1
        save_daily_stats(stats)
        return
        
    source_lang, confidence = detection
    is_short_text = len(original_text) <= SHORT_TEXT_BYPASS_LIMIT
    # Force translation if it's a short text and one of the target languages, despite low confidence
    is_target_lang_short = is_short_text and source_lang in [LANG_A_CODE.lower(), LANG_B_CODE.lower()]
    
    logger.info(f"Language detection -> Source Lang: {source_lang.upper()} | Confidence: {confidence:.1%} | "
                 f"Length: {len(original_text)} | Short Text Bypass: {is_target_lang_short}")
                 
    # Skip if confidence is too low and it's not a short text bypass case
    if confidence < CONFIDENCE_THRESHOLD and not is_target_lang_short:
        logger.info(f"Confidence too low, skipping translation -> {confidence:.1%}")
        return
    
    reply_parts = []
    success_type = None
    # Inline icon for visual enhancement
    ICON_TRANSLATE = "️🌐"
    
    # 4. Execute Translation Logic
    
    # Check for Language A (e.g., zh-CN, also matches zh)
    if source_lang.startswith(LANG_A_CODE.lower().split("-")[0]) or source_lang == LANG_A_CODE.lower():
        # Language A -> Language B
        translated = await translate_text(original_text, LANG_B_CODE, client)
        if translated:
            reply_parts.append(f"{ICON_TRANSLATE} *{LANG_A_NAME} -> {LANG_B_NAME}* {LANG_B_FLAG}\n{translated}")
            success_type = "a_to_b"
            logger.success(f"Translation successful | {LANG_A_NAME} -> {LANG_B_NAME} | Translation: \"{translated}\"")
            
    # Check for Language B
    elif source_lang == LANG_B_CODE.lower():
        # Language B -> Language A
        translated = await translate_text(original_text, LANG_A_CODE, client)
        if translated:
            reply_parts.append(f"{ICON_TRANSLATE} *{LANG_B_NAME} -> {LANG_A_NAME}* {LANG_A_FLAG}\n{translated}")
            success_type = "b_to_a"
            logger.success(f"Translation successful | {LANG_B_NAME} -> {LANG_A_NAME} | Translation: \"{translated}\"")
            
    else:
        # Other language -> Dual Translation (to both A and B)
        task_a = translate_text(original_text, LANG_A_CODE, client)
        task_b = translate_text(original_text, LANG_B_CODE, client)
        # Concurrently execute both translation tasks
        result_a, result_b = await asyncio.gather(task_a, task_b)
        
        if result_a:
            reply_parts.append(f"{ICON_TRANSLATE} *{source_lang.upper()} -> {LANG_A_NAME}* {LANG_A_FLAG}\n{result_a}")
        if result_b:
            reply_parts.append(f"{ICON_TRANSLATE} *{source_lang.upper()} -> {LANG_B_NAME}* {LANG_B_FLAG}\n{result_b}")
            
        if result_a and result_b:
            success_type = "other_to_dual"
            logger.success(f"Dual translation successful | {source_lang.upper()} -> {LANG_A_NAME} Trans: {result_a} | {LANG_B_NAME} Trans: {result_b}")
        elif result_a or result_b:
            success_type = "partial_dual"
            logger.warning(f"Partial dual translation successful | {LANG_A_NAME}: {bool(result_a)} Trans: {result_a} | {LANG_B_NAME}: {bool(result_b)} Trans: {result_b}")
    
    # 5. Send Reply + Update Statistics
    if reply_parts:
        reply_text = "\n\n".join(reply_parts)
        # Reply with Markdown formatting, referencing the original message
        await msg.reply_text(
            reply_text, 
            parse_mode="Markdown", 
            disable_web_page_preview=True, 
            reply_to_message_id=msg.message_id, 
            disable_notification=True
        )
        await asyncio.sleep(REPLY_DELAY_SECONDS) # Small delay to avoid API flood/spam
        
        stats["total"] += 1
        if success_type:
            stats[success_type] += 1
        logger.success(f"Translation No. {stats['total']} successful | Type: {success_type} | Chat_ID: {chat_id}")
    else:
        stats["failed"] += 1
        logger.error(f"Translation completely failed | Original Text: \"{original_text}\" | Source Lang: {source_lang}")
        
    save_daily_stats(stats)
    context.bot_data["daily_stats"] = stats # Update the in-memory data


# ==================================== Main Program Execution ====================================

def main():
    """Main function to initialize and run the Telegram bot."""
    # Initialize the Google Translate client first
    client = create_translation_client()

    # Configure the Application builder for high concurrency
    app = Application.builder() \
        .token(BOT_TOKEN) \
        .concurrent_updates(True) \
        .connection_pool_size(64) \
        .http_version("1.1") \
        .build()

    # Inject global data into the bot_data dictionary
    app.bot_data["translate_client"] = client
    app.bot_data["daily_stats"] = load_daily_stats()

    # Register handlers
    # Message handler: Filters for text or caption messages that are NOT commands
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND & (
            filters.ChatType.GROUPS |
            filters.ChatType.SUPERGROUP |
            filters.ChatType.PRIVATE
        ),
        # Wrap the handler to inject the request_id for logging
        with_request_id(handle_message)
    ))
    # Command handler for /stats
    app.add_handler(CommandHandler("stats", stats_command))

    # Error handling
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        """Logs all unhandled errors."""
        logger.error(f"Uncaught exception: {context.error}", exc_info=True)
    app.add_error_handler(error_handler)

    # Startup logs
    logger.success("Dual-Language Translation Bot started successfully!")
    logger.success(f"Current translation pair -> {LANG_A_NAME}({LANG_A_CODE}) <-> {LANG_B_NAME}({LANG_B_CODE})")
    if ALLOWED_CHAT_IDS:
        logger.info(f"Group whitelist enabled: {list(ALLOWED_CHAT_IDS)}")
    if ALLOWED_ADMIN_USERNAMES:
        logger.info(f"Admin usernames: {[f'@{u}' for u in ALLOWED_ADMIN_USERNAMES]}")
    logger.info("Use /stats to view today's statistics (Admin only)")

    # Start the bot, dropping any pending updates from when the bot was offline
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()