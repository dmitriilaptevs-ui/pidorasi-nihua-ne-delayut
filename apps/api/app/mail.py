"""Outgoing email: console transport for development, SMTP for deployments.

Verification and reset links contain one-time secrets. The console transport
prints them only outside production; in production it refuses and logs a
warning instead, so tokens never leak into container logs.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage
from typing import Protocol

from .settings import settings

log = logging.getLogger("rubai.mail")


class Mailer(Protocol):
    async def send(self, *, to: str, subject: str, text: str) -> None: ...


class ConsoleMailer:
    async def send(self, *, to: str, subject: str, text: str) -> None:
        if settings.app_env == "production":
            log.warning("console mail transport in production; message to %s suppressed", to)
            return
        log.info("mail to=%s subject=%s\n%s", to, subject, text)


class SmtpMailer:
    async def send(self, *, to: str, subject: str, text: str) -> None:
        from aiosmtplib import send as smtp_send  # imported lazily; unused in dev

        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        implicit_tls = settings.smtp_port == 465
        await smtp_send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls and not implicit_tls,
            use_tls=implicit_tls,
            timeout=15,
        )


def get_mailer() -> Mailer:
    if settings.mail_transport == "smtp":
        return SmtpMailer()
    return ConsoleMailer()
