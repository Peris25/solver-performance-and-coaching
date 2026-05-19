"""SMTP email sending for solver coaching reports.

The portal builds a coaching .docx and attaches it to a short, friendly
email naming the solver's focus areas in plain words. The text is
generated from their stats, not from a generic template.

Configuration comes from environment variables (see config.py — SMTP_*).
If smtp_host is empty, send_coaching_email() raises EmailNotConfigured
so the UI can surface a clean error.
"""
from __future__ import annotations
import io
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from app.config import settings


class EmailNotConfigured(RuntimeError):
    """Raised when SMTP settings are missing."""
    pass


def _format_hours(h):
    if h is None:
        return "—"
    if h < 1:
        return f"{round(h * 60)} min"
    return f"{h:.1f} h"


def _format_pct(p):
    return "—" if p is None else f"{p * 100:.0f}%"


def _build_subject(stats: dict, period_label: str) -> str:
    focus = stats.get("focus_areas") or []
    if "strong" in focus:
        return f"{period_label} — strong work this month"
    if not focus:
        return f"{period_label} — your performance report"
    return f"{period_label} — coaching focus this month"


def _build_body_text(stats: dict, period_label: str) -> str:
    """Plain-text email body. Encouraging, names the focus areas with numbers."""
    first_name = stats["name"].split()[0]
    vol = stats.get("volume", 0)
    rating = stats.get("avg_rating")
    n_ratings = stats.get("n_ratings", 0)
    focus = stats.get("focus_areas") or []
    sub_rate = stats.get("submission_rate")
    rtat = stats.get("median_response_tat_hrs")

    rating_str = f"{rating:.2f}/5" if rating and n_ratings >= 3 else None

    # Opening line
    parts = [f"Hi {first_name},\n"]
    headline = f"You completed {vol} valuations this month"
    if rating_str:
        headline += f", with a client rating of {rating_str}"
    headline += "."
    parts.append(headline)

    # Focus line
    if "strong" in focus:
        parts.append(
            "\nYou're hitting your targets across the board — that's the result of "
            "consistent habits, not luck. The attached report has more detail."
        )
    elif "time" in focus and "submission" in focus:
        parts.append(
            f"\nThe data flags two areas to focus on this month: time and submission rate. "
            f"Median response TAT was {_format_hours(rtat)} (target 4h) and "
            f"submission rate was {_format_pct(sub_rate)} (target 85%). "
            f"The attached report has three specific habits for each — you've got this."
        )
    elif "time" in focus:
        parts.append(
            f"\nThe one area to focus on this month is time. Your median response TAT "
            f"was {_format_hours(rtat)} against a 4-hour target. The attached report "
            f"breaks down where time usually leaks and three habits that close the gap."
        )
    elif "submission" in focus:
        parts.append(
            f"\nThe one area to focus on this month is submission rate. You submitted "
            f"{_format_pct(sub_rate)} of assigned jobs (target 85%). The attached report "
            f"shows where the gap is and three habits to close it."
        )
    elif "rating" in focus:
        parts.append(
            f"\nClient rating is below target this month at {rating_str or '—'} "
            f"(target ≥ 4.5). Your team lead will follow up directly — small changes "
            f"in how visits are presented usually move the rating up quickly."
        )
    else:
        parts.append("\nThe attached report has the full breakdown.")

    parts.append("\nThe attached Word doc has your full scorecard and the steps to try.")
    parts.append("\nBest regards,\nSolvit Operations")

    return "\n".join(parts)


def _build_body_html(stats: dict, period_label: str) -> str:
    """Simple HTML version of the body. Same content, slightly richer formatting."""
    first_name = stats["name"].split()[0]
    vol = stats.get("volume", 0)
    rating = stats.get("avg_rating")
    n_ratings = stats.get("n_ratings", 0)
    focus = stats.get("focus_areas") or []
    sub_rate = stats.get("submission_rate")
    rtat = stats.get("median_response_tat_hrs")
    rating_str = f"{rating:.2f}/5" if rating and n_ratings >= 3 else None

    headline = f"You completed <strong>{vol}</strong> valuations this month"
    if rating_str:
        headline += f", with a client rating of <strong>{rating_str}</strong>"
    headline += "."

    if "strong" in focus:
        focus_html = (
            "<p>You're hitting your targets across the board — that's the result of "
            "consistent habits, not luck. The attached report has more detail.</p>"
        )
    elif "time" in focus and "submission" in focus:
        focus_html = (
            f"<p>The data flags two areas to focus on this month: <strong>time</strong> "
            f"and <strong>submission rate</strong>. Median response TAT was "
            f"<strong>{_format_hours(rtat)}</strong> (target 4h) and submission rate was "
            f"<strong>{_format_pct(sub_rate)}</strong> (target 85%). The attached report "
            f"has three specific habits for each — you've got this.</p>"
        )
    elif "time" in focus:
        focus_html = (
            f"<p>The one area to focus on this month is <strong>time</strong>. Your median "
            f"response TAT was <strong>{_format_hours(rtat)}</strong> against a 4-hour target. "
            f"The attached report breaks down where time usually leaks and three habits that "
            f"close the gap.</p>"
        )
    elif "submission" in focus:
        focus_html = (
            f"<p>The one area to focus on this month is <strong>submission rate</strong>. "
            f"You submitted <strong>{_format_pct(sub_rate)}</strong> of assigned jobs "
            f"(target 85%). The attached report shows where the gap is and three habits to close it.</p>"
        )
    elif "rating" in focus:
        focus_html = (
            f"<p>Client rating is below target this month at "
            f"<strong>{rating_str or '—'}</strong> (target ≥ 4.5). Your team lead will follow "
            f"up directly — small changes in how visits are presented usually move the rating up quickly.</p>"
        )
    else:
        focus_html = "<p>The attached report has the full breakdown.</p>"

    return f"""<html>
<body style="font-family: -apple-system, system-ui, sans-serif; color: #1a1a1a; max-width: 600px; line-height: 1.6;">
  <p>Hi {first_name},</p>
  <p>{headline}</p>
  {focus_html}
  <p>The attached Word doc has your full scorecard and the steps to try.</p>
  <p style="margin-top: 24px;">Best regards,<br><strong>Solvit Operations</strong></p>
</body>
</html>"""


def send_coaching_email(
    to_email: str,
    solver_name: str,
    period_label: str,
    stats: dict,
    docx_bytes: bytes,
) -> None:
    """Send a coaching email with the docx attached. Raises on failure."""
    if not settings.smtp_host:
        raise EmailNotConfigured(
            "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, "
            "and SMTP_FROM_EMAIL in your environment."
        )
    if not settings.smtp_from_email:
        raise EmailNotConfigured("SMTP_FROM_EMAIL is required.")

    msg = EmailMessage()
    msg["Subject"] = _build_subject(stats, period_label)
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to_email
    msg.set_content(_build_body_text(stats, period_label))
    msg.add_alternative(_build_body_html(stats, period_label), subtype="html")

    # Attach the docx
    safe_name = "".join(c if c.isalnum() else "_" for c in solver_name).strip("_")
    filename = f"{safe_name}_coaching_{period_label.replace(' ', '_')}.docx"
    msg.add_attachment(
        docx_bytes,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )

    # Send
    if settings.smtp_use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.starttls(context=context)
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
