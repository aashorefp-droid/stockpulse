"""
config.py — Centralized keys, tokens, and credentials.

All scripts import from here.
Environment variables (if set) take priority.
If a local .env file exists, it will be loaded automatically.
"""

import os

# Automatically load .env file if present (for local development)
_env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_file):
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
    except Exception:
        pass

# ── Telegram Alert Bot (stock_pulse.py notifications) ───────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEFAULT_TELEGRAM_BOT_TOKEN = os.getenv("DEFAULT_TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Telegram Watchlist Bot (telegram_watchlist.py) ──────────────────────────
WATCHLIST_BOT_TOKEN = os.getenv("WATCHLIST_BOT_TOKEN", "")
WATCHLIST_CHAT_ID = os.getenv("WATCHLIST_CHAT_ID", "")

# ── Alpaca API (real-time market data) ──────────────────────────────────────
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")

# ── Alpaca Paper Trading API ───────────────────────────────────────────────
ALPACA_PAPER_API_KEY = os.getenv("ALPACA_PAPER_API_KEY", "")
ALPACA_PAPER_API_SECRET = os.getenv("ALPACA_PAPER_API_SECRET", "")
ALPACA_PAPER_BASE_URL = os.getenv("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")

# ── Polygon API (earnings / delayed bars) ──────────────────────────────────
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

# ── Finnhub API (earnings calendar) ────────────────────────────────────────
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# ── Alpha Vantage ──────────────────────────────────────────────────────────
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
