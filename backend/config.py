import os
from pathlib import Path

# Automatically load local .env if present
try:
    from dotenv import load_dotenv
    _root_env = Path(__file__).resolve().parent.parent / ".env"
    _backend_env = Path(__file__).resolve().parent / ".env"
    if _backend_env.exists():
        load_dotenv(_backend_env, override=False)
    if _root_env.exists():
        load_dotenv(_root_env, override=False)
    load_dotenv(override=False)
except ImportError:
    pass

def _get_clean_env(key: str, default: str = "") -> str:
    val = os.getenv(key, default)
    if val is None:
        return default
    return str(val).strip().strip('"').strip("'")

ALPACA_API_KEY    = _get_clean_env("ALPACA_API_KEY",    "AKBTFWNEQOAQ6YTHVSEBZHSERS")
ALPACA_API_SECRET = _get_clean_env("ALPACA_API_SECRET", "9r3XFuhPUcaouy4Vin38D3zeT8ksYxM8tmvj6PwhQzDF")
POLYGON_API_KEY   = _get_clean_env("POLYGON_API_KEY",   "q4Sx_c3RB9LeUkX4_efYSjFqi4dWtBHz")
ALPACA_DATA_BASE  = "https://data.alpaca.markets"

# ── Telegram Watchlist & Alerts ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = _get_clean_env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = _get_clean_env("TELEGRAM_CHAT_ID", "")
TELEGRAM_GROUP_CHAT_ID = _get_clean_env("TELEGRAM_GROUP_CHAT_ID", "")
TELEGRAM_MESSAGE_THREAD_ID = _get_clean_env("TELEGRAM_MESSAGE_THREAD_ID", "")
TELEGRAM_MACRO_MESSAGE_THREAD_ID = _get_clean_env("TELEGRAM_MACRO_MESSAGE_THREAD_ID", "")
TELEGRAM_SPY_INTRADAY_MESSAGE_THREAD_ID = _get_clean_env("TELEGRAM_SPY_INTRADAY_MESSAGE_THREAD_ID", "")
TELEGRAM_SWING_MESSAGE_THREAD_ID = _get_clean_env("TELEGRAM_SWING_MESSAGE_THREAD_ID", "")
WATCHLIST_BOT_TOKEN = _get_clean_env("WATCHLIST_BOT_TOKEN", "")
WATCHLIST_CHAT_ID   = _get_clean_env("WATCHLIST_CHAT_ID", "")

# ── ThinkOrSwim (TOS) Gmail Watchlist ────────────────────────────────────────
GMAIL_USER         = os.getenv("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
GMAIL_IMAP_HOST    = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com").strip()
TOS_EMAIL_FROM     = os.getenv("TOS_EMAIL_FROM", "thinkorswim.com").strip()
TOS_EMAIL_SUBJECT  = os.getenv("TOS_EMAIL_SUBJECT", "Alert:").strip()
TOS_LOOKBACK_DAYS  = int(os.getenv("TOS_LOOKBACK_DAYS", "3"))
