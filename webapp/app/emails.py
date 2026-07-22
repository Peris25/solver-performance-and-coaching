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
        return f"{period_label} — thank you for the strong month"
    if not focus:
        return f"{period_label} — your performance summary"
    return f"{period_label} — a few things to work on this month"


def _format_target_hours(targets: dict | None) -> str:
    """Format the total TAT target — '10h', etc."""
    if not targets:
        return "10h"
    t = targets.get("total_tat_hours_max", 10.0)
    if t == int(t):
        return f"{int(t)}h"
    return f"{t:.1f}h"


def _build_strong_performer_lines(stats: dict, targets: dict | None) -> tuple[str, str]:
    """Recognition body for solvers who hit every target. Returns (text, html)."""
    total_tat = stats.get("avg_total_tat_hrs")
    sub_rate = stats.get("submission_rate")
    rating = stats.get("avg_rating")
    n_ratings = stats.get("n_ratings", 0)
    vol = stats.get("volume", 0)
    tat_target = _format_target_hours(targets)

    highlights = []
    if vol:
        highlights.append(f"Total Valued: **{vol} valuations** completed this month")
    if total_tat is not None:
        highlights.append(
            f"On average it took you **{total_tat:.1f} hours** from when a job "
            f"was assigned to when you submitted it (the target is {tat_target})"
        )
    if sub_rate is not None and stats.get("assigned_count", 0) >= 5:
        highlights.append(
            f"You finished **{_format_pct(sub_rate)} of the jobs** assigned to "
            f"you (the target is 85%)"
        )
    if rating is not None and n_ratings >= 3:
        highlights.append(
            f"Your clients rated you **{rating:.2f} out of 5** "
            f"({n_ratings} ratings this month)"
        )

    bullets_text = "\n".join(f"  • {h.replace('**', '')}" for h in highlights)
    bullets_html = "".join(
        f"<li>{h.replace('**', '<strong>', 1).replace('**', '</strong>', 1)}</li>"
        for h in highlights
    )

    text = (
        "Thank you for the strong work this month. You hit every target, and that "
        "kind of consistency really matters — it keeps the team running and our "
        "clients happy.\n\n"
        "Here's what you did:\n"
        f"{bullets_text}\n\n"
        "That doesn't happen by accident. It comes from habits you've built: calling "
        "clients before traveling, taking photos in the same order every time, and "
        "carrying yourself well on site. Don't change those.\n\n"
        "When you're ready for the next step:\n"
        "  • Take on harder jobs — fleet vehicles, commercial trucks, post-accident assessments.\n"
        "  • Help a newer solver. Twenty minutes on a call with someone struggling saves them weeks.\n"
        "  • Tell us what you notice. You see more cars than most people — if you spot a pattern, share it.\n\n"
        "The attached document has your full scorecard."
    )

    html = (
        "<p>Thank you for the strong work this month. You hit every target, and "
        "that kind of consistency really matters — it keeps the team running and "
        "our clients happy.</p>"
        "<p><strong>Here's what you did:</strong></p>"
        f"<ul>{bullets_html}</ul>"
        "<p>That doesn't happen by accident. It comes from habits you've built: "
        "calling clients before traveling, taking photos in the same order every "
        "time, and carrying yourself well on site. Don't change those.</p>"
        "<p><strong>When you're ready for the next step:</strong></p>"
        "<ul>"
        "<li>Take on harder jobs — fleet vehicles, commercial trucks, post-accident assessments.</li>"
        "<li>Help a newer solver. Twenty minutes on a call with someone struggling saves them weeks.</li>"
        "<li>Tell us what you notice. You see more cars than most people — if you spot a pattern, share it.</li>"
        "</ul>"
        "<p>The attached document has your full scorecard.</p>"
    )
    return text, html


def _build_body_text(stats: dict, period_label: str, targets: dict | None = None) -> str:
    """Plain-text email body. Simple, direct, no jargon."""
    first_name = stats["name"].split()[0]
    vol = stats.get("volume", 0)
    rating = stats.get("avg_rating")
    n_ratings = stats.get("n_ratings", 0)
    focus = stats.get("focus_areas") or []
    sub_rate = stats.get("submission_rate")
    assigned = stats.get("assigned_count", 0)
    pending = stats.get("pending_count", 0)
    total_tat = stats.get("avg_total_tat_hrs")
    tat_target = _format_target_hours(targets)

    rating_str = f"{rating:.2f}/5" if rating and n_ratings >= 3 else None

    parts = [f"Hi {first_name},\n"]

    # Strong performers: recognition email
    if "strong" in focus:
        body, _ = _build_strong_performer_lines(stats, targets)
        parts.append(body)
        parts.append("\nBest regards,\nSolvit Operations")
        return "\n".join(parts)

    # Headline — what they did
    headline = f"This month you completed {vol} valuations"
    if rating_str:
        headline += f". Your clients rated you {rating_str}"
    headline += "."
    parts.append(headline)

    # Plain-language coaching by focus area
    if "time" in focus and "submission" in focus:
        tat_str = f"{total_tat:.1f} hours" if total_tat else "—"
        parts.append(
            f"\nTwo things to work on this month:\n\n"
            f"1) How long jobs take you. On average, it took you {tat_str} from "
            f"when a job was assigned to when you submitted it. The target is "
            f"{tat_target}.\n\n"
            f"2) Finishing the jobs you accept. You were given {assigned} jobs, "
            f"finished {vol}, and {pending} are still pending. The target is to "
            f"close at least 85% of what's assigned to you.\n\n"
            f"The attached document explains in plain language what to do for each."
        )
    elif "time" in focus:
        tat_str = f"{total_tat:.1f} hours" if total_tat else "—"
        gap = ""
        if total_tat:
            try:
                tt = float(tat_target.replace("h", ""))
                if total_tat > tt:
                    gap = f" That's about {total_tat - tt:.1f} hours longer than the goal."
            except (ValueError, TypeError):
                pass
        parts.append(
            f"\nThe main thing to work on this month is how long jobs take you. "
            f"On average, it took you {tat_str} from when a job was assigned to "
            f"when you submitted it. The target is {tat_target}.{gap}\n\n"
            f"The attached document explains what's eating the time and three "
            f"specific things you can change."
        )
    elif "submission" in focus:
        sub_str = _format_pct(sub_rate)
        parts.append(
            f"\nThe main thing to work on this month is finishing the jobs you accept. "
            f"You were given {assigned} jobs and finished {vol} ({sub_str}). "
            f"{pending} are still pending in your basket. The target is 85%.\n\n"
            f"The attached document shows where the gap is and three things that "
            f"will help close it."
        )
    elif "rating" in focus:
        parts.append(
            f"\nYour clients rated you {rating_str or '—'} this month. The team is "
            f"aiming for 4.5 or higher. This isn't about your workflow — it's about "
            f"how you come across to clients. Your team lead will talk to you about "
            f"this directly. The small things (greeting, explaining what you're "
            f"doing, closing the visit well) usually move the rating up quickly."
        )
    else:
        parts.append("\nThe attached document has your full numbers.")

    parts.append("\nThe attached Word document has your full scorecard.")
    parts.append("\nBest regards,\nSolvit Operations")

    return "\n".join(parts)


def _build_body_html(stats: dict, period_label: str, targets: dict | None = None) -> str:
    """HTML body — same plain-language content with light formatting."""
    first_name = stats["name"].split()[0]
    vol = stats.get("volume", 0)
    rating = stats.get("avg_rating")
    n_ratings = stats.get("n_ratings", 0)
    focus = stats.get("focus_areas") or []
    sub_rate = stats.get("submission_rate")
    assigned = stats.get("assigned_count", 0)
    pending = stats.get("pending_count", 0)
    total_tat = stats.get("avg_total_tat_hrs")
    tat_target = _format_target_hours(targets)
    rating_str = f"{rating:.2f}/5" if rating and n_ratings >= 3 else None

    if "strong" in focus:
        _, html_body = _build_strong_performer_lines(stats, targets)
        return f"""<html>
<body style="font-family: -apple-system, system-ui, sans-serif; color: #1a1a1a; max-width: 600px; line-height: 1.6;">
  <p>Hi {first_name},</p>
  {html_body}
  <p style="margin-top: 24px;">Best regards,<br><strong>Solvit Operations</strong></p>
</body>
</html>"""

    headline = f"This month you completed <strong>{vol}</strong> valuations"
    if rating_str:
        headline += f". Your clients rated you <strong>{rating_str}</strong>"
    headline += "."

    if "time" in focus and "submission" in focus:
        tat_str = f"{total_tat:.1f} hours" if total_tat else "—"
        focus_html = (
            "<p><strong>Two things to work on this month:</strong></p>"
            "<ol>"
            f"<li><strong>How long jobs take you.</strong> On average it took you "
            f"<strong>{tat_str}</strong> from when a job was assigned to when you "
            f"submitted it. The target is <strong>{tat_target}</strong>.</li>"
            f"<li><strong>Finishing the jobs you accept.</strong> You were given "
            f"<strong>{assigned} jobs</strong>, finished <strong>{vol}</strong>, "
            f"and <strong>{pending}</strong> are still pending. The target is to "
            f"close at least 85%.</li>"
            "</ol>"
            "<p>The attached document explains in plain language what to do for each.</p>"
        )
    elif "time" in focus:
        tat_str = f"{total_tat:.1f} hours" if total_tat else "—"
        gap = ""
        if total_tat:
            try:
                tt = float(tat_target.replace("h", ""))
                if total_tat > tt:
                    gap = f" That's about <strong>{total_tat - tt:.1f} hours longer than the goal</strong>."
            except (ValueError, TypeError):
                pass
        focus_html = (
            f"<p>The main thing to work on this month is <strong>how long jobs take you</strong>. "
            f"On average it took you <strong>{tat_str}</strong> from when a job was "
            f"assigned to when you submitted it. The target is <strong>{tat_target}</strong>.{gap}</p>"
            f"<p>The attached document explains what's eating the time and three "
            f"specific things you can change.</p>"
        )
    elif "submission" in focus:
        sub_str = _format_pct(sub_rate)
        focus_html = (
            f"<p>The main thing to work on this month is <strong>finishing the jobs "
            f"you accept</strong>. You were given <strong>{assigned} jobs</strong> "
            f"and finished <strong>{vol}</strong> ({sub_str}). "
            f"<strong>{pending}</strong> are still pending in your basket. The target is 85%.</p>"
            f"<p>The attached document shows where the gap is and three things that "
            f"will help close it.</p>"
        )
    elif "rating" in focus:
        focus_html = (
            f"<p>Your clients rated you <strong>{rating_str or '—'}</strong> this month. "
            f"The team is aiming for <strong>4.5 or higher</strong>. This isn't about "
            f"your workflow — it's about how you come across to clients. "
            f"<strong>Your team lead will talk to you about this directly.</strong> The small "
            f"things (greeting, explaining what you're doing, closing the visit well) "
            f"usually move the rating up quickly.</p>"
        )
    else:
        focus_html = "<p>The attached document has your full numbers.</p>"

    return f"""<html>
<body style="font-family: -apple-system, system-ui, sans-serif; color: #1a1a1a; max-width: 600px; line-height: 1.6;">
  <p>Hi {first_name},</p>
  <p>{headline}</p>
  {focus_html}
  <p>The attached Word document has your full scorecard.</p>
  <p style="margin-top: 24px;">Best regards,<br><strong>Solvit Operations</strong></p>
</body>
</html>"""


def build_preview(
    stats: dict,
    period_label: str,
    targets: dict | None = None,
) -> dict:
    """Return the subject, plain-text body, and HTML body that would be sent.

    Used by the admin editing UI so the admin can review and tweak
    before clicking Send.
    """
    return {
        "subject": _build_subject(stats, period_label),
        "body_text": _build_body_text(stats, period_label, targets),
        "body_html": _build_body_html(stats, period_label, targets),
        "focus_areas": stats.get("focus_areas") or [],
    }


def build_template(focus: str, targets: dict | None = None) -> dict:
    """Return a generic editable template for one focus area.

    The template uses {{tokens}} so the admin can write once and it
    is applied to every solver with that focus area during bulk send.
    Tokens resolved at send time: {{name}}, {{first_name}},
    {{volume}}, {{avg_tat}}, {{tat_target}}, {{assigned}},
    {{completed}}, {{pending}}, {{submission_pct}}, {{rating}}.
    """
    tat_target = targets.get("total_tat_hours_max", 10.0) if targets else 10.0
    tgt_str = f"{int(tat_target)}h" if tat_target == int(tat_target) else f"{tat_target:.1f}h"

    if focus == "time":
        subject = "{{period_label}} — a few things to work on this month"
        body = (
            "Hi {{first_name}},\n\n"
            "This month you completed {{volume}} valuations. "
            "Your clients rated you {{rating}}/5.\n\n"
            "The main thing to work on is how long jobs take you. "
            "On average it took you {{avg_tat}} from when a job was assigned "
            "to when you submitted it. The target is " + tgt_str + ".\n\n"
            "Three things that will help:\n\n"
            "1. Accept or decline the job within 5 minutes of getting the alert "
            "— don't let it sit.\n\n"
            "2. Don't let jobs sit overnight. If you can't get to it the same day, "
            "decline it so someone else can.\n\n"
            "3. Same routine every visit: photos → exterior → interior → submit "
            "before leaving the site.\n\n"
            "The attached Word document has your full scorecard.\n\n"
            "Best regards,\nSolvit Operations"
        )
    elif focus == "submission":
        subject = "{{period_label}} — a few things to work on this month"
        body = (
            "Hi {{first_name}},\n\n"
            "This month you completed {{volume}} valuations. "
            "Your clients rated you {{rating}}/5.\n\n"
            "The main thing to work on is finishing the jobs you accept. "
            "You were given {{assigned}} jobs and finished {{completed}} ({{submission_pct}}). "
            "{{pending}} are still pending. The target is 85%.\n\n"
            "Three things that will help:\n\n"
            "1. Before driving to a client, call them first to confirm they have "
            "their documents and the vehicle ready.\n\n"
            "2. If the client doesn't pick up the first time, try again at a "
            "different time of day before giving up.\n\n"
            "3. When you mark a job as pending, write the exact time you'll try "
            "again — 'tomorrow 9 AM', not 'later'.\n\n"
            "The attached Word document has your full scorecard.\n\n"
            "Best regards,\nSolvit Operations"
        )
    elif focus == "recognition":
        subject = "{{period_label}} — thank you for the strong month"
        body = (
            "Hi {{first_name}},\n\n"
            "Thank you for the strong work this month. "
            "You completed {{volume}} valuations and your clients rated you {{rating}}/5. "
            "You hit every target — that's the result of habits you've built, "
            "and it makes a real difference to the team.\n\n"
            "Keep doing what works. When you're ready for more, "
            "take on harder jobs, help a newer solver, or flag patterns you notice.\n\n"
            "The attached Word document has your full scorecard.\n\n"
            "Best regards,\nSolvit Operations"
        )
    else:
        subject = "{{period_label}} — your performance summary"
        body = (
            "Hi {{first_name}},\n\n"
            "This month you completed {{volume}} valuations. "
            "Your clients rated you {{rating}}/5.\n\n"
            "The attached Word document has your full scorecard.\n\n"
            "Best regards,\nSolvit Operations"
        )

    return {"focus": focus, "subject": subject, "body": body}


def apply_template(template_body: str, stats: dict, period_label: str) -> str:
    """Fill {{tokens}} in a template body with a solver's actual numbers."""
    name = stats.get("name", "")
    first_name = name.split()[0] if name else ""
    vol = stats.get("volume", 0)
    avg_tat = stats.get("avg_total_tat_hrs")
    avg_tat_str = f"{avg_tat:.1f}h" if avg_tat is not None else "—"
    rating = stats.get("avg_rating")
    rating_str = f"{rating:.2f}" if rating and stats.get("n_ratings", 0) >= 3 else "—"
    assigned = stats.get("assigned_count", 0)
    completed = stats.get("valued_count", 0)
    pending = stats.get("pending_count", 0)
    sub_rate = stats.get("submission_rate")
    sub_pct = f"{sub_rate*100:.0f}%" if sub_rate is not None else "—"

    replacements = {
        "{{name}}": name,
        "{{first_name}}": first_name,
        "{{volume}}": str(vol),
        "{{avg_tat}}": avg_tat_str,
        "{{tat_target}}": "10h",
        "{{assigned}}": str(assigned),
        "{{completed}}": str(completed),
        "{{pending}}": str(pending),
        "{{submission_pct}}": sub_pct,
        "{{rating}}": rating_str,
        "{{period_label}}": period_label,
    }
    result = template_body
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result


def send_coaching_email(
    to_email: str,
    solver_name: str,
    period_label: str,
    stats: dict,
    docx_bytes: bytes,
    targets: dict | None = None,
    custom_body: str | None = None,
) -> None:
    """Send a coaching email with the docx attached. Raises on failure.

    If `custom_body` is provided (admin has edited the text), it is used as
    the plain-text body verbatim. The HTML version wraps it in minimal
    formatting. Otherwise the body is generated from the solver's stats.
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

    if custom_body:
        # Admin has edited the body — use it directly
        plain = custom_body
        html = (
            "<html><body style='font-family:-apple-system,system-ui,sans-serif;"
            "color:#1a1a1a;max-width:600px;line-height:1.6'>"
            + "".join(
                f"<p>{line}</p>" if line.strip() else ""
                for line in plain.split("\n")
            )
            + "</body></html>"
        )
    else:
        plain = _build_body_text(stats, period_label, targets)
        html = _build_body_html(stats, period_label, targets)

    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

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
