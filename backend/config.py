import os

ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY",    "AKBTFWNEQOAQ6YTHVSEBZHSERS")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "9r3XFuhPUcaouy4Vin38D3zeT8ksYxM8tmvj6PwhQzDF")
POLYGON_API_KEY   = os.getenv("POLYGON_API_KEY",   "q4Sx_c3RB9LeUkX4_efYSjFqi4dWtBHz")
ALPACA_DATA_BASE  = "https://data.alpaca.markets"

# ── Telegram Watchlist & Alerts ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()
WATCHLIST_BOT_TOKEN = os.getenv("WATCHLIST_BOT_TOKEN", "").strip()
WATCHLIST_CHAT_ID   = os.getenv("WATCHLIST_CHAT_ID", "").strip()

# ── ThinkOrSwim (TOS) Gmail Watchlist ────────────────────────────────────────
GMAIL_USER         = os.getenv("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
GMAIL_IMAP_HOST    = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com").strip()
TOS_EMAIL_FROM     = os.getenv("TOS_EMAIL_FROM", "thinkorswim.com").strip()
TOS_EMAIL_SUBJECT  = os.getenv("TOS_EMAIL_SUBJECT", "Alert: New symbol").strip()
TOS_LOOKBACK_DAYS  = int(os.getenv("TOS_LOOKBACK_DAYS", "2"))
