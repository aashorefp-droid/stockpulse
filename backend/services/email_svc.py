"""Small SMTP helper for scheduled email reports."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_email(subject: str, html_body: str, text_body: str = "") -> bool:
    """Send an HTML email. Defaults to Gmail SMTP with GMAIL_* credentials."""
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        port = 587
    user = os.getenv("SMTP_USER", "").strip() or os.getenv("GMAIL_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip() or os.getenv("GMAIL_APP_PASSWORD", "").strip()
    sender = os.getenv("HOLDINGS_EMAIL_FROM", "").strip() or user
    recipient = os.getenv("HOLDINGS_EMAIL_TO", "").strip() or user

    if not (host and port and user and password and sender and recipient):
        logger.warning("Email not configured: set GMAIL_USER/GMAIL_APP_PASSWORD and HOLDINGS_EMAIL_TO")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(text_body or "Open this email in an HTML-capable client.")
    msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        return False
