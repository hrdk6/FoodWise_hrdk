"""Notifications with channel failover and a circuit breaker per provider.

Every message is written to `outbox_messages` first, so there is a complete record of what
was sent to whom. Channels without a configured provider (WhatsApp and SMS need sender
registration in India) are recorded as `simulated`, which is what development uses and what
the in-app message log shows. Email goes out over SMTP when FOODWISE_SMTP_HOST is set.
"""

from __future__ import annotations

import logging
import smtplib
import time
import uuid
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy.orm import Session

from foodwise.config import get_settings
from foodwise.db.models import OutboxMessage

log = logging.getLogger("foodwise.notify")


class ProviderUnavailable(Exception):  # noqa: N818
    pass


@dataclass
class Recipient:
    label: str
    phone: str | None = None
    email: str | None = None


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown_s: float = 60.0) -> None:
        self.threshold, self.cooldown_s = threshold, cooldown_s
        self.failures: dict[str, int] = {}
        self.opened_at: dict[str, float] = {}

    def allow(self, name: str) -> bool:
        opened = self.opened_at.get(name)
        if opened is None:
            return True
        if time.monotonic() - opened > self.cooldown_s:
            self.opened_at.pop(name, None)
            self.failures[name] = 0
            return True
        return False

    def success(self, name: str) -> None:
        self.failures[name] = 0
        self.opened_at.pop(name, None)

    def failure(self, name: str) -> None:
        self.failures[name] = self.failures.get(name, 0) + 1
        if self.failures[name] >= self.threshold:
            self.opened_at[name] = time.monotonic()


breaker = CircuitBreaker()


def _send_email(to: str, subject: str, body: str) -> str:
    settings = get_settings()
    if not settings.smtp_host:
        return "simulated"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise ProviderUnavailable(str(exc)) from exc
    return "sent"


def _send_chat(channel: str, to: str, body: str) -> str:
    # WhatsApp Business, SMS and voice providers plug in here once sender IDs and templates
    # are approved. Until then the message is recorded but not transmitted.
    return "simulated"


def send(
    db: Session,
    *,
    tenant_id: uuid.UUID | None,
    recipient: Recipient,
    subject: str,
    body: str,
    link: str | None = None,
    channels: tuple[str, ...] = ("whatsapp", "sms", "email"),
    related: tuple[str, str] | None = None,
) -> OutboxMessage | None:
    """Try each channel in order until one succeeds; record the attempt in the outbox."""
    text_body = f"{body}\n{link}" if link else body
    last_error = None
    for channel in channels:
        address = recipient.email if channel == "email" else recipient.phone
        if not address:
            continue
        provider = "smtp" if channel == "email" else f"{channel}-provider"
        if not breaker.allow(provider):
            last_error = f"{provider} circuit open"
            continue
        msg = OutboxMessage(
            tenant_id=tenant_id,
            channel=channel,
            to_address=address,
            recipient_label=recipient.label,
            subject=subject,
            body=body,
            link=link,
            provider=provider,
            related_type=related[0] if related else None,
            related_id=related[1] if related else None,
        )
        try:
            msg.status = _send_email(address, subject, text_body) if channel == "email" else _send_chat(
                channel, address, text_body
            )
            breaker.success(provider)
        except ProviderUnavailable as exc:
            breaker.failure(provider)
            msg.status, msg.error = "failed", str(exc)
            db.add(msg)
            last_error = str(exc)
            continue
        msg.sent_at = msg.created_at
        db.add(msg)
        return msg
    log.warning("no channel delivered message to %s: %s", recipient.label, last_error)
    return None
