"""Transactional email (verification, password reset, trial reminders) over SMTP.

Disabled until SMTP_HOST / SMTP_USER / SMTP_PASSWORD / SMTP_FROM are set in the
environment. When disabled, callers get False and show an alternative (e.g. an
admin-generated reset link) instead of pretending an email was sent.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def configured() -> bool:
    return all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"))


def send(to: str, subject: str, body: str) -> bool:
    if not configured():
        return False
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(os.getenv("SMTP_PORT", "587"))
    try:
        if port == 465:
            with smtplib.SMTP_SSL(os.getenv("SMTP_HOST"), port, context=ssl.create_default_context(), timeout=20) as s:
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
                s.send_message(msg)
        else:
            with smtplib.SMTP(os.getenv("SMTP_HOST"), port, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
                s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        return False
