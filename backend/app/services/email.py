"""Render and deliver the daily digest email.

Two backends: "console" (prints the message — the default, needs no infra) and
"smtp" (sends via configured SMTP server).
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from html import escape

from app.config import settings
from app.models import Report, User

log = logging.getLogger("bitter.email")

_SEVERITY_LABEL = {"critical": "Critical", "medium": "Medium", "ignore": "Ignore"}
_SEVERITY_ORDER = ["critical", "medium", "ignore"]


def _group(report: Report) -> dict[str, list]:
    grouped: dict[str, list] = {s: [] for s in _SEVERITY_ORDER}
    for it in report.items:
        grouped.setdefault(it.severity, []).append(it)
    return grouped


def render_digest(user: User, report: Report) -> tuple[str, str, str]:
    grouped = _group(report)
    crit = len(grouped["critical"])
    subject = f"Bitter digest — {report.period_date} ({crit} critical)"

    text_lines = [f"Bitter — Engineering Intelligence digest for {report.period_date}", ""]
    if report.summary:
        text_lines += [report.summary, ""]
    html_parts = [
        f"<h2>Bitter digest — {escape(report.period_date)}</h2>",
        f"<p>{escape(report.summary or '')}</p>",
    ]
    for sev in _SEVERITY_ORDER:
        items = grouped.get(sev) or []
        if not items:
            continue
        label = _SEVERITY_LABEL[sev]
        text_lines.append(f"== {label} ==")
        html_parts.append(f"<h3>{label}</h3><ul>")
        for it in items:
            text_lines.append(f"* {it.title}")
            text_lines.append(f"    {it.body}")
            if it.action:
                text_lines.append(f"    Action: {it.action}")
            html_parts.append(
                f"<li><strong>{escape(it.title)}</strong><br>{escape(it.body)}"
                + (f"<br><em>Action: {escape(it.action)}</em>" if it.action else "")
                + "</li>"
            )
        text_lines.append("")
        html_parts.append("</ul>")

    if not any(grouped.values()):
        text_lines.append("Nothing needs your attention today.")
        html_parts.append("<p>Nothing needs your attention today.</p>")

    return subject, "\n".join(text_lines), "".join(html_parts)


def _deliver(to_addr: str, subject: str, text: str, html: str) -> None:
    if settings.email_backend == "console":
        log.info("EMAIL (console) -> %s\nSubject: %s\n%s", to_addr, subject, text)
        print(f"\n=== EMAIL to {to_addr} ===\nSubject: {subject}\n{text}\n=== END EMAIL ===\n")
        return

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password or "")
        server.send_message(msg)


def send_digest(user: User, report: Report) -> bool:
    """Deliver the digest if the user has an email and opted in. Returns sent?"""
    if not user.email or not user.email_opt_in:
        return False
    subject, text, html = render_digest(user, report)
    try:
        _deliver(user.email, subject, text, html)
        return True
    except Exception as exc:  # delivery must not break the pipeline
        log.warning("Email delivery failed for %s: %s", user.email, exc)
        return False
