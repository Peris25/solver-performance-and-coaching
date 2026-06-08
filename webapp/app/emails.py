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
        return f"{period_label} — recognition for strong performance"
    if not focus:
        return f"{period_label} — your performance report"
    return f"{period_label} — coaching focus this month"


def _format_target_hours(targets: dict | None) -> str:
    """Format the response TAT target nicely: '9h 30min', '10h', '9.5h'."""
    if not targets:
        return "9h 30min"
    t = targets.get("response_tat_hours_max", 9.5)
    if t == int(t):
        return f"{int(t)}h"
    if (t * 60) % 60 == 30:
        return f"{int(t)}h 30min"
    return f"{t:.1f}h"


def _build_strong_performer_lines(stats: dict, targets: dict | None) -> tuple[str, str]:
    """Build the recognition body for solvers hitting all targets. Returns (text, html)."""
    rtat = stats.get("median_response_tat_hrs")
    sub_rate = stats.get("submission_rate")
    rating = stats.get("avg_rating")
    n_ratings = stats.get("n_ratings", 0)
    vol = stats.get("volume", 0)
    tat_target = _format_target_hours(targets)

    # Pull out specific numbers the solver should feel proud of
    highlights = []
    if rtat is not None:
        highlights.append(f"Response TAT of **{_format_hours(rtat)}** (target ≤ {tat_target})")
    if sub_rate is not None and stats.get("assigned_count", 0) >= 5:
        highlights.append(f"Submission rate of **{_format_pct(sub_rate)}** (target ≥ 85%)")
    if rating is not None and n_ratings >= 3:
        highlights.append(f"Client rating of **{rating:.2f}/5** (target ≥ 4.5)")
    if vol:
        highlights.append(f"**{vol}** valuations completed")

    bullets_text = "\n".join(f"  • {h.replace('**', '')}" for h in highlights)
    bullets_html = "".join(f"<li>{h.replace('**', '<strong>', 1).replace('**', '</strong>', 1)}</li>" for h in highlights)

    text = (
        "Thank you for the strong work this month — you're hitting your targets across "
        "the board, and that consistency makes a real difference for the team and our "
        "clients.\n\n"
        "Your numbers this month:\n"
        f"{bullets_text}\n\n"
        "This isn't luck — it's the result of disciplined habits: prompt response, "
        "clean on-site workflow, and how you present yourself to clients. Keep doing "
        "exactly what's working.\n\n"
        "Where to grow next, when you're ready: take on harder jobs (fleet, heavy "
        "commercial, post-accident), mentor newer solvers — twenty minutes on a call "
        "saves them weeks — and flag patterns you notice across visits. You see more "
        "vehicles than most, and that perspective is valuable.\n\n"
        "The attached Word doc has your full scorecard."
    )

    html = (
        "<p>Thank you for the strong work this month — you're hitting your targets "
        "across the board, and that consistency makes a real difference for the team "
        "and our clients.</p>"
        "<p><strong>Your numbers this month:</strong></p>"
        f"<ul>{bullets_html}</ul>"
        "<p>This isn't luck — it's the result of disciplined habits: prompt response, "
        "clean on-site workflow, and how you present yourself to clients. Keep doing "
        "exactly what's working.</p>"
        "<p><strong>Where to grow next, when you're ready:</strong> take on harder jobs "
        "(fleet, heavy commercial, post-accident), mentor newer solvers — twenty "
        "minutes on a call saves them weeks — and flag patterns you notice across "
        "visits. You see more vehicles than most, and that perspective is valuable.</p>"
        "<p>The attached Word doc has your full scorecard.</p>"
    )
    return text, html


def _build_body_text(stats: dict, period_label: str, targets: dict | None = None) -> str:
    """Plain-text email body. Encouraging, names the focus areas with numbers."""
    first_name = stats["name"].split()[0]
    vol = stats.get("volume", 0)
    rating = stats.get("avg_rating")
    n_ratings = stats.get("n_ratings", 0)
    focus = stats.get("focus_areas") or []
    sub_rate = stats.get("submission_rate")
    rtat = stats.get("median_response_tat_hrs")
    tat_target = _format_target_hours(targets)

    rating_str = f"{rating:.2f}/5" if rating and n_ratings >= 3 else None

    parts = [f"Hi {first_name},\n"]

    # Strong performers get a recognition email, not a coaching one
    if "strong" in focus:
        body, _ = _build_strong_performer_lines(stats, targets)
        parts.append(body)
        parts.append("\nBest regards,\nSolvit Operations")
        return "\n".join(parts)

    # Otherwise, the coaching opening
    headline = f"You completed {vol} valuations this month"
    if rating_str:
        headline += f", with a client rating of {rating_str}"
    headline += "."
    parts.append(headline)

    # Focus-specific coaching body
    if "time" in focus and "submission" in focus:
        parts.append(
            f"\nThe data flags two areas to focus on this month: time and submission rate. "
            f"Median response TAT was {_format_hours(rtat)} (target {tat_target}) and "
            f"submission rate was {_format_pct(sub_rate)} (target 85%). "
            f"The attached report has three specific habits for each — you've got this."
        )
    elif "time" in focus:
        parts.append(
            f"\nThe one area to focus on this month is time. Your median response TAT "
            f"was {_format_hours(rtat)} against a {tat_target} target. The attached report "
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


def _build_body_html(stats: dict, period_label: str, targets: dict | None = None) -> str:
    """Simple HTML version of the body. Same content, slightly richer formatting."""
    first_name = stats["name"].split()[0]
    vol = stats.get("volume", 0)
    rating = stats.get("avg_rating")
    n_ratings = stats.get("n_ratings", 0)
    focus = stats.get("focus_areas") or []
    sub_rate = stats.get("submission_rate")
    rtat = stats.get("median_response_tat_hrs")
    rating_str = f"{rating:.2f}/5" if rating and n_ratings >= 3 else None
    tat_target = _format_target_hours(targets)

    # Strong performers get a recognition email
    if "strong" in focus:
        _, html_body = _build_strong_performer_lines(stats, targets)
        return f"""<html>
<body style="font-family: -apple-system, system-ui, sans-serif; color: #1a1a1a; max-width: 600px; line-height: 1.6;">
  <p>Hi {first_name},</p>
  {html_body}
  <p style="margin-top: 24px;">Best regards,<br><strong>Solvit Operations</strong></p>
</body>
</html>"""

    headline = f"You completed <strong>{vol}</strong> valuations this month"
    if rating_str:
        headline += f", with a client rating of <strong>{rating_str}</strong>"
    headline += "."

    if "time" in focus and "submission" in focus:
        focus_html = (
            f"<p>The data flags two areas to focus on this month: <strong>time</strong> "
            f"and <strong>submission rate</strong>. Median response TAT was "
            f"<strong>{_format_hours(rtat)}</strong> (target {tat_target}) and submission "
            f"rate was <strong>{_format_pct(sub_rate)}</strong> (target 85%). The attached "
            f"report has three specific habits for each — you've got this.</p>"
        )
    elif "time" in focus:
        focus_html = (
            f"<p>The one area to focus on this month is <strong>time</strong>. Your median "
            f"response TAT was <strong>{_format_hours(rtat)}</strong> against a {tat_target} "
            f"target. The attached report breaks down where time usually leaks and three "
            f"habits that close the gap.</p>"
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
    targets: dict | None = None,
) -> None:
    """Send a coaching email with the docx attached. Raises on failure.

    For solvers with `focus_areas == ["strong"]`, the email is a recognition
    note rather than coaching — same flow, different copy.
    """
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
    msg.set_content(_build_body_text(stats, period_label, targets))
    msg.add_alternative(_build_body_html(stats, period_label, targets), subtype="html")

    # Attach the docx — strong performers get a "recognition" filename
    focus = stats.get("focus_areas") or []
    doc_kind = "recognition" if "strong" in focus else "coaching"
    safe_name = "".join(c if c.isalnum() else "_" for c in solver_name).strip("_")
    filename = f"{safe_name}_{doc_kind}_{period_label.replace(' ', '_')}.docx"
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
