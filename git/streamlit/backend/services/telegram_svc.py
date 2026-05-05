"""Thin wrapper around the Telegram Bot API for sending alert messages."""
import logging
import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    try:
        r = requests.post(
            _BASE.format(token=bot_token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if not r.ok:
            logger.warning(f"Telegram rejected message: {r.text[:200]}")
        return r.ok
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False
