"""Thin wrapper around the Telegram Bot API for sending alert messages."""
import logging
import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
    message_thread_id: str | int | None = None,
) -> bool:
    """Send a Telegram message. Returns True on success."""
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if message_thread_id is not None and str(message_thread_id).strip():
            payload["message_thread_id"] = int(str(message_thread_id).strip())

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Telegram send payload: %s", payload)

        r = requests.post(
            _BASE.format(token=bot_token),
            json=payload,
            timeout=10,
        )
        if not r.ok:
            logger.warning(f"Telegram rejected message: {r.text[:200]}")
        else:
            logger.debug(
                "Telegram send success: chat_id=%s message_thread_id=%s",
                chat_id,
                payload.get("message_thread_id"),
            )
        return r.ok
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False
