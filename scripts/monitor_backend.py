#!/usr/bin/env python3
"""
StockPulse Backend Health Monitor & Telegram Alert Script.
Pings backend /health endpoint. If unreachable or down, sends a Telegram alert.
"""
import os
import sys
import time
import argparse
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# Add project root to path
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    import requests
except ImportError:
    print("requests not found; installing or run with requests available.")
    sys.exit(1)

from backend.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_MESSAGE_THREAD_ID,
)
from backend.services.telegram_svc import send_telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backend_monitor")

DEFAULT_URL = os.getenv("BACKEND_URL", "https://stockpulse-pkpj.onrender.com").rstrip("/")
DEFAULT_INTERVAL = int(os.getenv("MONITOR_INTERVAL_SEC", "60"))
DEFAULT_COOLDOWN = int(os.getenv("MONITOR_COOLDOWN_SEC", "900"))  # 15 minutes


def get_ct_time() -> str:
    try:
        return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %I:%M:%S %p CT")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_alert_message(text: str, token: str, chat_id: str, thread_id: str | None) -> bool:
    if not token or not chat_id:
        logger.warning("Telegram token or chat_id is missing.")
        return False
    return send_telegram(token, chat_id, text, message_thread_id=thread_id)


def ping_health(base_url: str, timeout: int = 12) -> tuple[bool, int, float, str]:
    target = f"{base_url}/health"
    start = time.time()
    try:
        r = requests.get(target, timeout=timeout)
        elapsed = round((time.time() - start) * 1000, 1)
        if r.status_code == 200:
            return True, r.status_code, elapsed, ""
        return False, r.status_code, elapsed, f"HTTP {r.status_code} {r.reason}"
    except requests.exceptions.Timeout:
        elapsed = round((time.time() - start) * 1000, 1)
        return False, 0, elapsed, f"Timeout after {timeout}s (Render sleeping or container frozen)"
    except requests.exceptions.ConnectionError as ce:
        elapsed = round((time.time() - start) * 1000, 1)
        return False, 0, elapsed, f"Connection refused / DNS failure: {str(ce)[:80]}"
    except Exception as e:
        elapsed = round((time.time() - start) * 1000, 1)
        return False, 0, elapsed, str(e)[:100]


def run_check(base_url: str, token: str, chat_id: str, thread_id: str | None, force_alert: bool = False) -> bool:
    is_ok, status_code, latency, err = ping_health(base_url)
    time_str = get_ct_time()
    target = f"{base_url}/health"

    if is_ok and not force_alert:
        logger.info(f"✓ Backend healthy: {target} (latency={latency}ms, status={status_code})")
        return True

    # Backend is DOWN (or force test)
    logger.warning(f"✗ Backend DOWN: {target} (status={status_code}, error={err})")
    msg = (
        "🚨 <b>StockPulse Backend ALERT — Server DOWN / Not Pingable</b>\n\n"
        f"⚠️ <b>Status:</b> Offline / Unreachable\n"
        f"📍 <b>Target:</b> <code>{target}</code>\n"
        f"❌ <b>Error:</b> {err or 'Simulated downtime test'}\n"
        f"⏱ <b>Latency:</b> {latency} ms\n"
        f"📅 <b>Detected at:</b> {time_str}\n\n"
        "💡 <i>Action: Check Render dashboard and logs. If on Render free tier, service may be sleeping or restarting.</i>"
    )

    sent = send_alert_message(msg, token, chat_id, thread_id)
    logger.info(f"Telegram down alert sent: {sent}")
    return False


def run_daemon(base_url: str, interval: int, cooldown: int, token: str, chat_id: str, thread_id: str | None):
    logger.info(f"Starting Backend Health Monitor daemon for {base_url} (interval={interval}s, cooldown={cooldown}s)")
    last_state = "unknown"
    last_alert_ts = 0

    while True:
        try:
            is_ok, status_code, latency, err = ping_health(base_url)
            now = time.time()
            time_str = get_ct_time()
            target = f"{base_url}/health"

            if is_ok:
                if last_state == "offline":
                    logger.info(f"Backend RESTORED! Sending recovery alert to Telegram...")
                    rec_msg = (
                        "✅ <b>StockPulse Backend RESTORED — Server ONLINE</b>\n\n"
                        "🟢 <b>Status:</b> Healthy & Pingable\n"
                        f"📍 <b>Target:</b> <code>{target}</code>\n"
                        f"⚡ <b>Latency:</b> {latency} ms\n"
                        f"📅 <b>Timestamp:</b> {time_str}\n\n"
                        "🚀 <i>All backend services, endpoints, and schedulers are reachable again.</i>"
                    )
                    send_alert_message(rec_msg, token, chat_id, thread_id)
                last_state = "online"
            else:
                elapsed_since_alert = now - last_alert_ts
                should_alert = last_state != "offline" or elapsed_since_alert >= cooldown
                if should_alert:
                    logger.warning(f"Backend DOWN! Sending alert to Telegram... (err: {err})")
                    down_msg = (
                        "🚨 <b>StockPulse Backend ALERT — Server DOWN / Not Pingable</b>\n\n"
                        "⚠️ <b>Status:</b> Offline / Unreachable\n"
                        f"📍 <b>Target:</b> <code>{target}</code>\n"
                        f"❌ <b>Error:</b> {err}\n"
                        f"⏱ <b>Latency:</b> {latency} ms\n"
                        f"📅 <b>Detected at:</b> {time_str}\n\n"
                        "💡 <i>Action: Check Render dashboard / logs. If on Render free tier, service may be sleeping or restarting.</i>"
                    )
                    if send_alert_message(down_msg, token, chat_id, thread_id):
                        last_alert_ts = now
                last_state = "offline"

        except Exception as e:
            logger.error(f"Error in monitor daemon loop: {e}")

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="StockPulse Backend Health Monitor")
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    parser.add_argument("--check-once", action="store_true", help="Perform a single health check")
    parser.add_argument("--test-alert", action="store_true", help="Send a test offline alert to Telegram")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous monitoring loop")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Daemon check interval in seconds")
    parser.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN, help="Alert cooldown in seconds")
    parser.add_argument("--token", default=TELEGRAM_BOT_TOKEN, help="Telegram bot token")
    parser.add_argument("--chat-id", default=TELEGRAM_CHAT_ID, help="Telegram chat ID")
    parser.add_argument("--thread-id", default=TELEGRAM_MESSAGE_THREAD_ID, help="Telegram message thread ID")

    args = parser.parse_args()

    if args.test_alert:
        print(f"Sending test alert for {args.url} to chat {args.chat_id} thread {args.thread_id}...")
        run_check(args.url, args.token, args.chat_id, args.thread_id, force_alert=True)
        return

    if args.daemon:
        run_daemon(args.url, args.interval, args.cooldown, args.token, args.chat_id, args.thread_id)
        return

    # Default to single check
    run_check(args.url, args.token, args.chat_id, args.thread_id)


if __name__ == "__main__":
    main()
