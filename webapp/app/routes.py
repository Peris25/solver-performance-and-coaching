"""API routes for the portal.

Grouped here rather than spread across files because there aren't enough
endpoints to justify a sub-package. Each route group is clearly labelled.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from pydantic import BaseModel
import io
import re
import shutil

from app import auth, analysis, models, reports
from app.database import get_db
from app.config import settings

router = APIRouter()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginPayload(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
def login(payload: LoginPayload, response: Response):
    if not auth.authenticate(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = auth.create_session_token()
    response.set_cookie(
        key=auth.SESSION_COOKIE_NAME,
        value=token,
        max_age=auth.session_max_age_seconds(),
        httponly=True,
        samesite="lax",
        secure=True,  # set True in production behind HTTPS
    )
    return {"ok": True, "user": "admin"}


@router.post("/api/auth/logout")
def logout(response: Response, _: str = Depends(auth.require_admin)):
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/api/auth/me")
def me(user: str = Depends(auth.require_admin)):
    return {"user": user}


# ---------------------------------------------------------------------------
# Upload — analyse a workbook and persist it as a Period + Snapshots
# ---------------------------------------------------------------------------

@router.post("/api/uploads")
async def upload_workbook(
    file: UploadFile = File(...),
    label: str = Form(...),
    db: Session = Depends(get_db),
    _: str = Depends(auth.require_admin),
):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only .xlsx/.xls files are accepted")

    # Save the upload to disk (so we can re-analyse later if needed)
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^\w.-]+", "_", label).strip("_") or "period"
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    saved_filename = f"{safe_label}_{timestamp}_{file.filename}"
    saved_path = upload_dir / saved_filename
    with open(saved_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    # Analyse
    try:
        result = analysis.analyse_workbook(saved_path)
    except ValueError as e:
        # Bad workbook shape — surface a clean message
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to analyse: {e}")

    # Upsert Period by label — re-uploading the same period replaces snapshots
    existing = db.scalar(select(models.Period).where(models.Period.label == label))
    if existing:
        # Delete old snapshots; will recreate
        for s in existing.snapshots:
            db.delete(s)
        period = existing
        period.uploaded_at = datetime.utcnow()
        period.uploaded_filename = saved_filename
    else:
        period = models.Period(
            label=label,
            uploaded_at=datetime.utcnow(),
            uploaded_filename=saved_filename,
        )
        db.add(period)
        db.flush()  # get period.id

    # Team fields
    t = result["team"]
    period.total_solvers = t["total_solvers"]
    period.total_valuations = t["total_valuations"]
    period.total_jobs_assigned = t.get("total_jobs_assigned") or 0
    period.total_jobs_pending = t.get("total_jobs_pending") or 0
    period.total_jobs_initiated_by_solvers = t.get("total_jobs_initiated_by_solvers") or 0
    period.team_submission_rate = t["team_submission_rate"]
    period.median_submission_rate = t["median_submission_rate"]
    period.avg_response_tat_hrs = t["avg_response_tat_hrs"]
    period.median_response_tat_hrs = t["median_response_tat_hrs"]
    period.avg_onsite_tat_hrs = t["avg_onsite_tat_hrs"]
    period.median_onsite_tat_hrs = t["median_onsite_tat_hrs"]
    period.avg_rating = t["avg_rating"]
    period.avg_volume = t["avg_volume"]
    period.median_volume = t["median_volume"]
    period.pct_stuck_jobs_team = t["pct_stuck_jobs_team"]

    # Solver snapshots
    for s in result["solvers"]:
        snap = models.SolverSnapshot(
            period_id=period.id,
            name=s["name"],
            volume=s["volume"],
            total_attempts=s["total_attempts"],
            valued_count=s["valued_count"],
            assigned_count=s["assigned_count"],
            pending_count=s["pending_count"],
            jobs_initiated=s.get("jobs_initiated", 0),
            stuck_job_count=s["stuck_job_count"],
            n_ratings=s["n_ratings"],
            submission_rate=s["submission_rate"],
            approval_rate=s["approval_rate"],
            avg_response_tat_hrs=s["avg_response_tat_hrs"],
            median_response_tat_hrs=s["median_response_tat_hrs"],
            avg_onsite_tat_hrs=s["avg_onsite_tat_hrs"],
            median_onsite_tat_hrs=s["median_onsite_tat_hrs"],
            avg_rating=s["avg_rating"],
            stuck_job_rate=s["stuck_job_rate"],
            classifications=s["classifications"],
            training_modules=s["training_modules"],
            focus_areas=s["focus_areas"],
            extra=s["extra"],
        )
        db.add(snap)

    db.commit()
    db.refresh(period)
    return {
        "ok": True,
        "period_id": period.id,
        "label": period.label,
        "total_solvers": period.total_solvers,
        "total_valuations": period.total_valuations,
    }


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

@router.get("/api/periods")
def list_periods(db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    rows = db.scalars(select(models.Period).order_by(desc(models.Period.uploaded_at))).all()
    return {"periods": [
        {
            "id": p.id,
            "label": p.label,
            "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
            "total_solvers": p.total_solvers,
            "total_valuations": p.total_valuations,
        }
        for p in rows
    ]}


@router.get("/api/periods/{period_id}")
def get_period(period_id: int, db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    p = db.get(models.Period, period_id)
    if not p:
        raise HTTPException(status_code=404, detail="Period not found")
    snapshots = sorted(p.snapshots, key=lambda s: s.volume, reverse=True)
    return {
        "id": p.id,
        "label": p.label,
        "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
        "team": {
            "total_solvers": p.total_solvers,
            "total_valuations": p.total_valuations,
            "total_jobs_assigned": p.total_jobs_assigned or 0,
            "total_jobs_pending": p.total_jobs_pending or 0,
            "total_jobs_initiated_by_solvers": p.total_jobs_initiated_by_solvers or 0,
            "team_submission_rate": p.team_submission_rate,
            "median_submission_rate": p.median_submission_rate,
            "avg_response_tat_hrs": p.avg_response_tat_hrs,
            "median_response_tat_hrs": p.median_response_tat_hrs,
            "avg_onsite_tat_hrs": p.avg_onsite_tat_hrs,
            "median_onsite_tat_hrs": p.median_onsite_tat_hrs,
            "avg_rating": p.avg_rating,
            "avg_volume": p.avg_volume,
            "median_volume": p.median_volume,
            "pct_stuck_jobs_team": p.pct_stuck_jobs_team,
        },
        "targets": analysis.TARGETS,
        "solvers": [snapshot_to_dict(s) for s in snapshots],
    }


@router.delete("/api/periods/{period_id}")
def delete_period(period_id: int, db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    p = db.get(models.Period, period_id)
    if not p:
        raise HTTPException(status_code=404, detail="Period not found")
    db.delete(p)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Solvers — per-period & history
# ---------------------------------------------------------------------------

def snapshot_to_dict(s: models.SolverSnapshot) -> dict:
    return {
        "name": s.name,
        "volume": s.volume,
        "total_attempts": s.total_attempts,
        "valued_count": s.valued_count,
        "assigned_count": s.assigned_count,
        "pending_count": s.pending_count,
        "jobs_initiated": s.jobs_initiated or 0,
        "stuck_job_count": s.stuck_job_count,
        "n_ratings": s.n_ratings,
        "submission_rate": s.submission_rate,
        "approval_rate": s.approval_rate,
        "avg_response_tat_hrs": s.avg_response_tat_hrs,
        "median_response_tat_hrs": s.median_response_tat_hrs,
        "avg_onsite_tat_hrs": s.avg_onsite_tat_hrs,
        "median_onsite_tat_hrs": s.median_onsite_tat_hrs,
        "avg_rating": s.avg_rating,
        "stuck_job_rate": s.stuck_job_rate,
        "classifications": s.classifications or {},
        "training_modules": s.training_modules or [],
        "focus_areas": s.focus_areas or [],
        "extra": s.extra or {},
    }


@router.get("/api/solvers/{name}/history")
def solver_history(name: str, db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    """All snapshots for a solver across periods, oldest first.

    Used for month-over-month trend charts in the UI.
    """
    snapshots = db.scalars(
        select(models.SolverSnapshot)
        .where(models.SolverSnapshot.name == name)
        .join(models.Period)
        .order_by(models.Period.uploaded_at.asc())
    ).all()
    return {
        "name": name,
        "periods": [
            {
                "period_id": s.period_id,
                "period_label": s.period.label,
                "uploaded_at": s.period.uploaded_at.isoformat() if s.period.uploaded_at else None,
                **snapshot_to_dict(s),
            }
            for s in snapshots
        ],
    }


# ---------------------------------------------------------------------------
# Reports — generate a per-solver Word doc on demand
# ---------------------------------------------------------------------------

class IntroPayload(BaseModel):
    intro: Optional[str] = None


@router.post("/api/periods/{period_id}/solvers/{name}/report")
def generate_report(
    period_id: int,
    name: str,
    payload: IntroPayload = IntroPayload(),
    db: Session = Depends(get_db),
    _: str = Depends(auth.require_admin),
):
    period = db.get(models.Period, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    snap = db.scalar(
        select(models.SolverSnapshot)
        .where(
            models.SolverSnapshot.period_id == period_id,
            models.SolverSnapshot.name == name,
        )
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Solver not in this period")

    docx_bytes = reports.build_doc(
        snapshot_to_dict(snap),
        team={
            "total_solvers": period.total_solvers,
            "total_valuations": period.total_valuations,
            "team_submission_rate": period.team_submission_rate,
            "median_submission_rate": period.median_submission_rate,
            "avg_response_tat_hrs": period.avg_response_tat_hrs,
            "median_response_tat_hrs": period.median_response_tat_hrs,
            "avg_onsite_tat_hrs": period.avg_onsite_tat_hrs,
            "median_onsite_tat_hrs": period.median_onsite_tat_hrs,
            "avg_rating": period.avg_rating,
            "avg_volume": period.avg_volume,
            "median_volume": period.median_volume,
            "pct_stuck_jobs_team": period.pct_stuck_jobs_team,
        },
        targets=analysis.TARGETS,
        period_label=period.label,
        personalised_intro=payload.intro,
    )

    safe_name = re.sub(r"[^\w]+", "_", name).strip("_")
    filename = f"{safe_name}_coaching_{period.label.replace(' ', '_')}.docx"

    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Solver emails — admin manages the name → email mapping here
# ---------------------------------------------------------------------------

class SolverEmailPayload(BaseModel):
    email: str


@router.get("/api/solver-emails")
def list_solver_emails(db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    rows = db.scalars(select(models.SolverEmail).order_by(models.SolverEmail.name)).all()
    return {"emails": [{"name": r.name, "email": r.email,
                        "updated_at": r.updated_at.isoformat()} for r in rows]}


@router.put("/api/solver-emails/{name}")
def upsert_solver_email(
    name: str,
    payload: SolverEmailPayload,
    db: Session = Depends(get_db),
    _: str = Depends(auth.require_admin),
):
    if "@" not in payload.email or "." not in payload.email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Invalid email address")

    existing = db.scalar(select(models.SolverEmail).where(models.SolverEmail.name == name))
    if existing:
        existing.email = payload.email
    else:
        db.add(models.SolverEmail(name=name, email=payload.email))
    db.commit()
    return {"ok": True, "name": name, "email": payload.email}


@router.delete("/api/solver-emails/{name}")
def delete_solver_email(name: str, db: Session = Depends(get_db),
                        _: str = Depends(auth.require_admin)):
    row = db.scalar(select(models.SolverEmail).where(models.SolverEmail.name == name))
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Email coaching reports — single send + bulk send to "didn't meet target"
# ---------------------------------------------------------------------------

class EmailSendResult(BaseModel):
    name: str
    status: str  # "sent" | "skipped_no_email" | "failed" | "skipped_strong"
    detail: Optional[str] = None


def _build_doc_for(period: models.Period, snap: models.SolverSnapshot) -> bytes:
    """Build the .docx bytes for one solver from their snapshot."""
    return reports.build_doc(
        snapshot_to_dict(snap),
        team={
            "total_solvers": period.total_solvers,
            "total_valuations": period.total_valuations,
            "team_submission_rate": period.team_submission_rate,
            "median_submission_rate": period.median_submission_rate,
            "avg_response_tat_hrs": period.avg_response_tat_hrs,
            "median_response_tat_hrs": period.median_response_tat_hrs,
            "avg_onsite_tat_hrs": period.avg_onsite_tat_hrs,
            "median_onsite_tat_hrs": period.median_onsite_tat_hrs,
            "avg_rating": period.avg_rating,
            "avg_volume": period.avg_volume,
            "median_volume": period.median_volume,
            "pct_stuck_jobs_team": period.pct_stuck_jobs_team,
        },
        targets=analysis.TARGETS,
        period_label=period.label,
        personalised_intro=None,
    )


def _email_one(period: models.Period, snap: models.SolverSnapshot,
               to_email: str, trigger: str, db: Session) -> EmailSendResult:
    """Send the coaching email to a single solver and record the result."""
    import app.emails as emails  # local import so the module can be missing in dev

    log = models.EmailSendLog(
        period_id=period.id,
        solver_name=snap.name,
        email_to=to_email,
        status="sent",
        trigger=trigger,
        focus_areas=snap.focus_areas or [],
    )
    try:
        docx_bytes = _build_doc_for(period, snap)
        emails.send_coaching_email(
            to_email=to_email,
            solver_name=snap.name,
            period_label=period.label,
            stats=snapshot_to_dict(snap),
            docx_bytes=docx_bytes,
        )
        log.status = "sent"
        db.add(log)
        db.commit()
        return EmailSendResult(name=snap.name, status="sent", detail=to_email)
    except emails.EmailNotConfigured as e:
        log.status = "failed"
        log.error = str(e)
        db.add(log)
        db.commit()
        return EmailSendResult(name=snap.name, status="failed", detail=str(e))
    except Exception as e:
        log.status = "failed"
        log.error = f"{type(e).__name__}: {e}"
        db.add(log)
        db.commit()
        return EmailSendResult(name=snap.name, status="failed", detail=str(e))


@router.post("/api/periods/{period_id}/solvers/{name}/email")
def email_one_solver(
    period_id: int,
    name: str,
    db: Session = Depends(get_db),
    _: str = Depends(auth.require_admin),
):
    """Send the coaching email for one solver. Returns an error if no email is on file."""
    period = db.get(models.Period, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    snap = db.scalar(select(models.SolverSnapshot).where(
        models.SolverSnapshot.period_id == period_id,
        models.SolverSnapshot.name == name,
    ))
    if not snap:
        raise HTTPException(status_code=404, detail="Solver not in this period")
    email_row = db.scalar(select(models.SolverEmail).where(models.SolverEmail.name == name))
    if not email_row:
        raise HTTPException(
            status_code=400,
            detail=f"No email on file for {name}. Add one in Solver Emails first.",
        )

    result = _email_one(period, snap, email_row.email, trigger="manual", db=db)
    if result.status == "failed":
        raise HTTPException(status_code=500, detail=result.detail)
    return result


@router.post("/api/periods/{period_id}/send-coaching-emails")
def bulk_send_coaching_emails(
    period_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(auth.require_admin),
):
    """Send coaching emails to every solver who didn't meet their targets this period.

    "Didn't meet targets" = has at least one classification = needs_work
    (i.e. any focus area set — time, submission, or rating).

    Skips solvers without an email on file and reports them so the admin
    can add the missing addresses.
    """
    period = db.get(models.Period, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")

    snaps = sorted(period.snapshots, key=lambda s: s.name)
    email_map = {
        r.name: r.email for r in
        db.scalars(select(models.SolverEmail)).all()
    }

    results: list[EmailSendResult] = []
    for snap in snaps:
        focus = snap.focus_areas or []
        # Strong performers (no needs_work flags) are excluded from the bulk send
        if not focus or focus == ["strong"]:
            results.append(EmailSendResult(
                name=snap.name, status="skipped_strong",
                detail="no focus areas flagged"
            ))
            continue
        to_email = email_map.get(snap.name)
        if not to_email:
            results.append(EmailSendResult(
                name=snap.name, status="skipped_no_email",
                detail="add email in Solver Emails"
            ))
            continue
        # Send
        result = _email_one(period, snap, to_email, trigger="bulk_targets_missed", db=db)
        results.append(result)

    return {
        "ok": True,
        "period_id": period_id,
        "results": [r.dict() for r in results],
        "summary": {
            "sent": sum(1 for r in results if r.status == "sent"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "skipped_no_email": sum(1 for r in results if r.status == "skipped_no_email"),
            "skipped_strong": sum(1 for r in results if r.status == "skipped_strong"),
        },
    }


@router.get("/api/periods/{period_id}/email-log")
def period_email_log(
    period_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(auth.require_admin),
):
    rows = db.scalars(
        select(models.EmailSendLog)
        .where(models.EmailSendLog.period_id == period_id)
        .order_by(desc(models.EmailSendLog.sent_at))
    ).all()
    return {
        "log": [{
            "id": r.id,
            "solver_name": r.solver_name,
            "email_to": r.email_to,
            "sent_at": r.sent_at.isoformat(),
            "status": r.status,
            "trigger": r.trigger,
            "error": r.error,
            "focus_areas": r.focus_areas,
        } for r in rows]
    }
