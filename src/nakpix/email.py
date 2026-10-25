"""Email via SMTP (config from env, no credentials in code).

Incident emails go to ``suporte@nakswap.xyz``. If sending fails, the incident
stays ``email_sent=0`` and the monitor retries with backoff — we never claim
an email was sent until the SMTP server accepts it.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate


def _cfg() -> dict:
    return {
        "host": os.environ.get("NAKPIX_SMTP_HOST", ""),
        "port": int(os.environ.get("NAKPIX_SMTP_PORT", "587")),
        "user": os.environ.get("NAKPIX_SMTP_USER", ""),
        "password": os.environ.get("NAKPIX_SMTP_PASS", ""),
        "from": os.environ.get("NAKPIX_SMTP_FROM", "nakpix@nakswap.xyz"),
        "to": os.environ.get("NAKPIX_SMTP_TO", "suporte@nakswap.xyz"),
        "tls": os.environ.get("NAKPIX_SMTP_TLS", "true").lower() in ("1", "true", "yes"),
    }


def is_configured() -> bool:
    return bool(_cfg()["host"])


def send_email(subject: str, body: str, to: str | None = None) -> bool:
    """Send one email. Returns True only if the server accepted it."""
    cfg = _cfg()
    if not cfg["host"]:
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = to or cfg["to"]
    msg["Date"] = formatdate(localtime=True)

    try:
        if cfg["port"] == 465:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
            if cfg["tls"]:
                server.starttls()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg["from"], [to or cfg["to"]], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"[email] falha ao enviar: {str(e)[:120]}", flush=True)
        return False
