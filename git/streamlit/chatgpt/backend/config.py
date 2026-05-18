"""
Runtime configuration — secrets come ONLY from environment variables.

No secret values are committed here. For local dev, put them in a gitignored
`.env` (see `.env.example`); in production set them in the Render dashboard
(Environment, sync:false). Missing required secrets are logged loudly at
startup but do not hard-crash the process (dependent features degrade).
"""
import os
import logging

logger = logging.getLogger(__name__)

# ── .env loading for local dev — dependency-free (works without python-dotenv) ──
def _load_env_file(path: str) -> int:
    """Minimal KEY=VALUE parser. Only sets vars not already in the env."""
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
                    n += 1
    except Exception:
        pass
    return n


_here = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.join(_here, ".env"),               # chatgpt/backend/.env
    os.path.join(_here, "..", ".env"),         # chatgpt/.env
    os.path.join(_here, "..", "..", ".env"),   # repo-root/.env
):
    if os.path.exists(_p):
        _load_env_file(_p)
        break  # first one wins

_missing: list[str] = []


def _req(name: str) -> str:
    """Required secret — env only. Records (doesn't raise) when unset."""
    v = os.getenv(name, "").strip()
    if not v:
        _missing.append(name)
    return v


def _opt(name: str, default: str) -> str:
    """Non-secret operational setting with a safe committed default."""
    return os.getenv(name, default)


# ── Secrets (env only — never commit values) ────────────────────────────────
ALPACA_API_KEY      = _req("ALPACA_API_KEY")
ALPACA_API_SECRET   = _req("ALPACA_API_SECRET")
POLYGON_API_KEY     = _req("POLYGON_API_KEY")

WATCHLIST_BOT_TOKEN = _req("WATCHLIST_BOT_TOKEN")
WATCHLIST_CHAT_ID   = _req("WATCHLIST_CHAT_ID")

GMAIL_USER          = _req("GMAIL_USER")
GMAIL_APP_PASSWORD  = _req("GMAIL_APP_PASSWORD")

# ── Non-secret operational config (safe to keep defaults in repo) ───────────
ALPACA_DATA_BASE    = _opt("ALPACA_DATA_BASE", "https://data.alpaca.markets")
GMAIL_IMAP_HOST     = _opt("GMAIL_IMAP_HOST", "imap.gmail.com")
TOS_EMAIL_FROM      = _opt("TOS_EMAIL_FROM", "thinkorswim.com")
TOS_EMAIL_SUBJECT   = _opt("TOS_EMAIL_SUBJECT", "Alert: New symbol")  # "" = any
TOS_LOOKBACK_DAYS   = int(_opt("TOS_LOOKBACK_DAYS", "2"))

if _missing:
    logger.warning(
        "config: missing env var(s): %s — dependent features will be "
        "disabled until set (Render dashboard env / local .env).",
        ", ".join(_missing),
    )
