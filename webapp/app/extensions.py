"""New subsystems layered on top of the original performance portal:

  - Fleet sourcing bonus (mirrors SOLVERS__Fleet_Bonus_Calculator.xlsx)
  - Complaints / accidents log
  - Broadcast messaging with attachments (the "send through Outlook" ask —
    see the note on send_broadcast_email in app/emails.py for how to swap
    this for a direct Microsoft Graph "send as" integration later)
  - Admin-editable settings (fleet bonus rate/minimum, TAT travel allowance,
    dormancy threshold) so policy changes don't need a redeploy
  - Active/dormant solver monitoring
  - Performance-change flags across the two most recent periods
  - Annual/quarterly recap (JSON + Word doc)
  - CSV/XLSX exports for the solver table, talent grid, regions, fleet
    bonus and complaints

Kept in its own router/module rather than appended to the already-large
routes.py, and included from main.py alongside it.
"""
from __future__ import annotations
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel
import openpyxl
import pandas as pd

from app import auth, analysis, models, reports
from app.database import get_db
from app.config import settings
from app.emails import send_broadcast_email, EmailNotConfigured
from app.routes import snapshot_to_dict  # reuse the existing serializer
from app.settings_store import get_setting, set_setting, SETTING_DEFAULTS

router = APIRouter()


# ---------------------------------------------------------------------------
# Settings — admin-editable policy knobs, stored as AppSetting rows
# ---------------------------------------------------------------------------

@router.get("/api/settings/{key}")
def read_setting(key: str, db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    if key not in SETTING_DEFAULTS:
        raise HTTPException(status_code=404, detail="Unknown setting")
    return {"key": key, "value": get_setting(db, key)}


@router.put("/api/settings/{key}")
def write_setting(key: str, value: dict, db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    if key not in SETTING_DEFAULTS:
        raise HTTPException(status_code=404, detail="Unknown setting")
    return {"key": key, "value": set_setting(db, key, value)}


# ---------------------------------------------------------------------------
# Fleet sourcing bonus
# ---------------------------------------------------------------------------

def _recompute_fleet_row(entry: models.FleetEntry, rate: float, min_size: int) -> None:
    result = analysis.compute_fleet_row(
        entry.fleet_size_booked, entry.price_per_vehicle, entry.approved_count,
        client_satisfied=bool(entry.client_satisfied), rate=rate, min_size=min_size,
    )
    entry.rate = result["rate"]
    entry.billed_amount = result["billed_amount"]
    entry.bonus_amount = result["bonus_amount"]
    if result["qualifies"] and entry.status == "open":
        entry.status = "closed"


def _fleet_to_dict(e: models.FleetEntry) -> dict:
    return {
        "id": e.id,
        "fleet_client_name": e.fleet_client_name,
        "solver_name": e.solver_name,
        "fleet_size_booked": e.fleet_size_booked,
        "price_per_vehicle": e.price_per_vehicle,
        "approved_count": e.approved_count,
        "client_satisfied": bool(e.client_satisfied),
        "rate": e.rate,
        "billed_amount": e.billed_amount,
        "bonus_amount": e.bonus_amount,
        "status": e.status,
        "notes": e.notes,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


class FleetEntryPayload(BaseModel):
    fleet_client_name: str
    solver_name: str
    fleet_size_booked: int = 0
    price_per_vehicle: float = 0.0
    approved_count: int = 0
    client_satisfied: bool = False
    notes: Optional[str] = None


@router.get("/api/fleet-bonus")
def list_fleet_entries(db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    entries = db.scalars(select(models.FleetEntry).order_by(models.FleetEntry.created_at.desc())).all()
    total_bonus = sum((e.bonus_amount or 0) for e in entries)
    total_billed = sum((e.billed_amount or 0) for e in entries)
    return {
        "entries": [_fleet_to_dict(e) for e in entries],
        "totals": {"billed_amount": total_billed, "bonus_amount": total_bonus, "count": len(entries)},
    }


@router.post("/api/fleet-bonus")
def create_fleet_entry(payload: FleetEntryPayload, db: Session = Depends(get_db),
                        _: str = Depends(auth.require_admin)):
    cfg = get_setting(db, "fleet_bonus")
    entry = models.FleetEntry(
        fleet_client_name=payload.fleet_client_name,
        solver_name=payload.solver_name,
        fleet_size_booked=payload.fleet_size_booked,
        price_per_vehicle=payload.price_per_vehicle,
        approved_count=payload.approved_count,
        client_satisfied=1 if payload.client_satisfied else 0,
        notes=payload.notes,
    )
    _recompute_fleet_row(entry, cfg.get("rate"), cfg.get("min_size"))
    db.add(entry)
    db.commit()
    return _fleet_to_dict(entry)


@router.put("/api/fleet-bonus/{entry_id}")
def update_fleet_entry(entry_id: int, payload: FleetEntryPayload, db: Session = Depends(get_db),
                        _: str = Depends(auth.require_admin)):
    entry = db.get(models.FleetEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Fleet entry not found")
    cfg = get_setting(db, "fleet_bonus")
    entry.fleet_client_name = payload.fleet_client_name
    entry.solver_name = payload.solver_name
    entry.fleet_size_booked = payload.fleet_size_booked
    entry.price_per_vehicle = payload.price_per_vehicle
    entry.approved_count = payload.approved_count
    entry.client_satisfied = 1 if payload.client_satisfied else 0
    entry.notes = payload.notes
    _recompute_fleet_row(entry, cfg.get("rate"), cfg.get("min_size"))
    db.commit()
    return _fleet_to_dict(entry)


@router.delete("/api/fleet-bonus/{entry_id}")
def delete_fleet_entry(entry_id: int, db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    entry = db.get(models.FleetEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Fleet entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}


@router.post("/api/fleet-bonus/{entry_id}/mark-paid")
def mark_fleet_entry_paid(entry_id: int, db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    entry = db.get(models.FleetEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Fleet entry not found")
    entry.status = "paid"
    db.commit()
    return _fleet_to_dict(entry)


@router.post("/api/fleet-bonus/import")
async def import_fleet_workbook(file: UploadFile = File(...), db: Session = Depends(get_db),
                                 _: str = Depends(auth.require_admin)):
    """Import rows from a workbook laid out like SOLVERS__Fleet_Bonus_Calculator.xlsx
    (Fleet / Client, Sourced by, Fleet size booked, Price per vehicle (Ksh),
    Approved count, Client satisfied?). The example/instruction rows (no
    solver name) are skipped automatically."""
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only .xlsx/.xls files are accepted")
    content = await file.read()
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.worksheets[0]

    # Find the header row (the one containing "Sourced by")
    header_row_idx = None
    for row in ws.iter_rows(min_row=1, max_row=min(20, ws.max_row)):
        values = [str(c.value).strip() if c.value else "" for c in row]
        if any("sourced by" in v.lower() for v in values):
            header_row_idx = row[0].row
            headers = values
            break
    if header_row_idx is None:
        raise HTTPException(status_code=400, detail="Could not find the header row (expected a 'Sourced by' column)")

    def _col(name_fragment):
        for i, h in enumerate(headers):
            if name_fragment.lower() in h.lower():
                return i
        return None

    idx = {
        "client": _col("Fleet / Client") or _col("Client"),
        "solver": _col("Sourced by"),
        "size": _col("Fleet size"),
        "price": _col("Price per vehicle"),
        "approved": _col("Approved count"),
        "satisfied": _col("Client satisfied"),
    }

    cfg = get_setting(db, "fleet_bonus")
    created = 0
    skipped = 0
    for row in ws.iter_rows(min_row=header_row_idx + 1, max_row=ws.max_row):
        values = [c.value for c in row]
        solver = values[idx["solver"]] if idx["solver"] is not None else None
        client = values[idx["client"]] if idx["client"] is not None else None
        if not solver or not client or "example" in str(client).lower():
            skipped += 1
            continue
        satisfied_raw = values[idx["satisfied"]] if idx["satisfied"] is not None else None
        entry = models.FleetEntry(
            fleet_client_name=str(client),
            solver_name=str(solver),
            fleet_size_booked=int(values[idx["size"]] or 0) if idx["size"] is not None else 0,
            price_per_vehicle=float(values[idx["price"]] or 0) if idx["price"] is not None else 0.0,
            approved_count=int(values[idx["approved"]] or 0) if idx["approved"] is not None else 0,
            client_satisfied=1 if str(satisfied_raw).strip().lower() == "yes" else 0,
        )
        _recompute_fleet_row(entry, cfg.get("rate"), cfg.get("min_size"))
        db.add(entry)
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------------------
# Complaints / accidents
# ---------------------------------------------------------------------------

def _complaint_to_dict(c: models.Complaint) -> dict:
    return {
        "id": c.id,
        "kind": c.kind,
        "solver_name": c.solver_name,
        "region": c.region,
        "date_occurred": c.date_occurred.isoformat() if c.date_occurred else None,
        "severity": c.severity,
        "description": c.description,
        "reported_by": c.reported_by,
        "status": c.status,
        "resolution_notes": c.resolution_notes,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


class ComplaintPayload(BaseModel):
    kind: str = "complaint"
    solver_name: str
    region: Optional[str] = None
    date_occurred: Optional[str] = None
    severity: str = "medium"
    description: str
    reported_by: Optional[str] = None
    status: str = "open"
    resolution_notes: Optional[str] = None


def _parse_date(s: Optional[str]):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


@router.get("/api/complaints")
def list_complaints(
    solver_name: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    _: str = Depends(auth.require_admin),
):
    q = select(models.Complaint)
    if solver_name:
        q = q.where(models.Complaint.solver_name == solver_name)
    if kind:
        q = q.where(models.Complaint.kind == kind)
    if status:
        q = q.where(models.Complaint.status == status)
    rows = db.scalars(q.order_by(models.Complaint.created_at.desc())).all()
    return {"complaints": [_complaint_to_dict(c) for c in rows]}


@router.post("/api/complaints")
def create_complaint(payload: ComplaintPayload, db: Session = Depends(get_db),
                      _: str = Depends(auth.require_admin)):
    c = models.Complaint(
        kind=payload.kind, solver_name=payload.solver_name, region=payload.region,
        date_occurred=_parse_date(payload.date_occurred), severity=payload.severity,
        description=payload.description, reported_by=payload.reported_by,
        status=payload.status, resolution_notes=payload.resolution_notes,
    )
    db.add(c)
    db.commit()
    return _complaint_to_dict(c)


@router.put("/api/complaints/{complaint_id}")
def update_complaint(complaint_id: int, payload: ComplaintPayload, db: Session = Depends(get_db),
                      _: str = Depends(auth.require_admin)):
    c = db.get(models.Complaint, complaint_id)
    if not c:
        raise HTTPException(status_code=404, detail="Complaint not found")
    c.kind = payload.kind
    c.solver_name = payload.solver_name
    c.region = payload.region
    c.date_occurred = _parse_date(payload.date_occurred)
    c.severity = payload.severity
    c.description = payload.description
    c.reported_by = payload.reported_by
    c.status = payload.status
    c.resolution_notes = payload.resolution_notes
    db.commit()
    return _complaint_to_dict(c)


@router.delete("/api/complaints/{complaint_id}")
def delete_complaint(complaint_id: int, db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    c = db.get(models.Complaint, complaint_id)
    if not c:
        raise HTTPException(status_code=404, detail="Complaint not found")
    db.delete(c)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Active / dormant solver monitoring
# ---------------------------------------------------------------------------

@router.get("/api/solvers/activity")
def solver_activity(db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    """Flags each registered solver active/dormant based on whether they
    show zero valued jobs across the last N uploaded periods (N is the
    admin-editable "dormancy" setting, default 2)."""
    cfg = get_setting(db, "dormancy")
    n_periods = cfg.get("periods", analysis.TARGETS["dormancy_periods"])

    periods = db.scalars(select(models.Period).order_by(models.Period.uploaded_at.asc())).all()
    recent_periods = periods[-n_periods:] if periods else []

    recent_volumes: dict[str, list[int]] = {}
    for p in recent_periods:
        snaps = {s.name: (s.volume or 0) for s in p.snapshots}
        for name, vol in snaps.items():
            recent_volumes.setdefault(name, []).append(vol)
        # registered solvers who didn't appear at all in this upload get an explicit 0
    registered = db.scalars(select(models.Solver)).all()
    names = [s.name for s in registered]
    for name in names:
        vols = recent_volumes.setdefault(name, [])
        while len(vols) < len(recent_periods):
            vols.append(0)

    result = analysis.detect_dormancy(names, recent_volumes, dormancy_periods=n_periods)
    by_solver = {s.name: s for s in registered}
    out = []
    for name, info in result.items():
        s = by_solver.get(name)
        out.append({
            "name": name,
            "region": s.region if s else None,
            "active_flag": bool(s.active) if s else True,
            **info,
        })
    out.sort(key=lambda r: (r["status"] != "dormant", r["name"]))
    return {
        "periods_checked": [p.label for p in recent_periods],
        "dormancy_threshold_periods": n_periods,
        "solvers": out,
        "dormant_count": sum(1 for r in out if r["status"] == "dormant"),
        "active_count": sum(1 for r in out if r["status"] == "active"),
    }


# ---------------------------------------------------------------------------
# Performance-change flags across the two most recent periods
# ---------------------------------------------------------------------------

@router.get("/api/solvers/performance-flags")
def performance_flags(db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    """Auto-compares the two most recent uploaded periods and returns which
    solvers moved significantly, without the caller needing to know period
    IDs — this is what badges the dashboard with 'improved' / 'declined'."""
    periods = db.scalars(select(models.Period).order_by(models.Period.uploaded_at.desc())).all()
    if len(periods) < 2:
        return {"available": False, "reason": "Need at least two uploaded periods to compare.", "flags": []}

    current, previous = periods[0], periods[1]
    cur_snaps = {s.name: snapshot_to_dict(s) for s in current.snapshots}
    prev_snaps = {s.name: snapshot_to_dict(s) for s in previous.snapshots}

    flags = []
    for name in sorted(set(cur_snaps) & set(prev_snaps)):
        cmp = analysis.compare_snapshots(cur_snaps[name], prev_snaps[name])
        if not cmp:
            continue
        sig = analysis.flag_significant_changes(cmp)
        if sig["verdict"] == "steady":
            continue
        flags.append({
            "name": name,
            "verdict": sig["verdict"],
            "significant_improved": sig["significant_improved"],
            "significant_declined": sig["significant_declined"],
            "headline": cmp.get("headline"),
        })
    flags.sort(key=lambda f: (f["verdict"] != "declined", f["name"]))
    return {
        "available": True,
        "current_period": current.label,
        "previous_period": previous.label,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Annual / quarterly recap
# ---------------------------------------------------------------------------

def _recap_snapshots(db: Session, name: str, year: Optional[int], quarter: Optional[int]) -> list[dict]:
    snapshots = db.scalars(
        select(models.SolverSnapshot)
        .where(models.SolverSnapshot.name == name)
        .join(models.Period)
        .order_by(models.Period.uploaded_at.asc())
    ).all()
    out = []
    for s in snapshots:
        uploaded_at = s.period.uploaded_at
        if year is not None and uploaded_at.year != year:
            continue
        if quarter is not None:
            q = (uploaded_at.month - 1) // 3 + 1
            if q != quarter:
                continue
        d = snapshot_to_dict(s)
        d["period_label"] = s.period.label
        out.append(d)
    return out


@router.get("/api/solvers/{name}/recap")
def get_recap(name: str, year: Optional[int] = None, quarter: Optional[int] = None,
              db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    snaps = _recap_snapshots(db, name, year, quarter)
    window_label = "All time"
    if year and quarter:
        window_label = f"Q{quarter} {year}"
    elif year:
        window_label = str(year)
    recap = analysis.build_recap(name, snaps)
    recap["window_label"] = window_label
    return recap


@router.get("/api/solvers/{name}/recap/report")
def get_recap_report(name: str, year: Optional[int] = None, quarter: Optional[int] = None,
                      db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    snaps = _recap_snapshots(db, name, year, quarter)
    if not snaps:
        raise HTTPException(status_code=404, detail="No data for this solver in that window")
    window_label = "All time"
    if year and quarter:
        window_label = f"Q{quarter} {year}"
    elif year:
        window_label = str(year)
    recap = analysis.build_recap(name, snaps)
    doc_bytes = reports.build_recap_doc(recap, name, window_label)
    safe_name = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    filename = f"{safe_name}_recap_{window_label.replace(' ', '_')}.docx"
    return StreamingResponse(
        io.BytesIO(doc_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Broadcast messaging (with attachments) — the "communication system"
# ---------------------------------------------------------------------------

@router.get("/api/broadcast/recipients")
def broadcast_recipients(db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    """All solvers with an email on file, grouped by region, for the
    recipient picker (all / by region / individual)."""
    emails = {e.name: e.email for e in db.scalars(select(models.SolverEmail)).all()}
    solvers = db.scalars(select(models.Solver)).all()
    out = []
    for s in solvers:
        out.append({
            "name": s.name, "region": s.region, "active": bool(s.active),
            "email": emails.get(s.name), "has_email": s.name in emails,
        })
    out.sort(key=lambda r: r["name"])
    return {"solvers": out, "regions": sorted({s.region for s in solvers})}


@router.post("/api/broadcast")
async def send_broadcast(
    subject: str = Form(...),
    body: str = Form(...),
    recipients: str = Form(...),  # comma-separated solver names, or "all"
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _: str = Depends(auth.require_admin),
):
    """Send a custom message (with optional document attachments) to one,
    several, or all solvers with an email on file. Every send is logged to
    EmailSendLog (trigger="broadcast") for an audit trail."""
    emails = {e.name: e.email for e in db.scalars(select(models.SolverEmail)).all()}

    if recipients.strip().lower() == "all":
        target_names = list(emails.keys())
    else:
        target_names = [n.strip() for n in recipients.split(",") if n.strip()]

    attachments = []
    for f in files:
        content = await f.read()
        attachments.append((f.filename, content, f.content_type or "application/octet-stream"))
    attachment_names = [a[0] for a in attachments]

    results = []
    for name in target_names:
        to_email = emails.get(name)
        log = models.EmailSendLog(
            period_id=None, solver_name=name, email_to=to_email or "",
            trigger="broadcast", subject=subject, attachments=attachment_names, focus_areas=[],
        )
        if not to_email:
            log.status = "skipped"
            log.error = "No email on file for this solver"
            db.add(log)
            results.append({"name": name, "status": "skipped_no_email"})
            continue
        try:
            send_broadcast_email(to_email, subject, body, attachments=attachments)
            log.status = "sent"
            results.append({"name": name, "status": "sent"})
        except EmailNotConfigured as e:
            log.status = "failed"
            log.error = str(e)
            results.append({"name": name, "status": "failed", "detail": str(e)})
        except Exception as e:  # pragma: no cover - network/SMTP errors
            log.status = "failed"
            log.error = str(e)
            results.append({"name": name, "status": "failed", "detail": str(e)})
        db.add(log)
    db.commit()
    return {"sent": sum(1 for r in results if r["status"] == "sent"), "results": results}


@router.get("/api/broadcast/log")
def broadcast_log(db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    rows = db.scalars(
        select(models.EmailSendLog)
        .where(models.EmailSendLog.trigger == "broadcast")
        .order_by(models.EmailSendLog.sent_at.desc())
        .limit(500)
    ).all()
    return {
        "log": [
            {
                "id": r.id, "solver_name": r.solver_name, "email_to": r.email_to,
                "subject": r.subject, "status": r.status, "error": r.error,
                "attachments": r.attachments or [], "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Exports — CSV/XLSX for any tabular view the admin wants to take offline
# ---------------------------------------------------------------------------

def _rows_to_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    if not rows:
        return b""
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _rows_to_xlsx(rows: list[dict], sheet_name: str = "Export") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name[:31] or "Export"
    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _export_response(rows: list[dict], fmt: str, filename_stem: str) -> StreamingResponse:
    fmt = (fmt or "csv").lower()
    if fmt == "xlsx":
        content = _rows_to_xlsx(rows, filename_stem)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{filename_stem}.xlsx"
    else:
        content = _rows_to_csv(rows)
        media = "text/csv"
        filename = f"{filename_stem}.csv"
    return StreamingResponse(
        io.BytesIO(content), media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _flatten_snapshot_row(name: str, d: dict) -> dict:
    tg = d.get("talent_grid") or {}
    return {
        "solver_name": name,
        "volume": d.get("volume"),
        "assigned_count": d.get("assigned_count"),
        "jobs_initiated": d.get("jobs_initiated"),
        "backlog_count": d.get("backlog_count"),
        "submission_rate": d.get("submission_rate"),
        "avg_total_tat_hrs": d.get("avg_total_tat_hrs"),
        "avg_response_tat_hrs": d.get("avg_response_tat_hrs"),
        "avg_onsite_tat_hrs": d.get("avg_onsite_tat_hrs"),
        "avg_rating": d.get("avg_rating"),
        "n_ratings": d.get("n_ratings"),
        "focus_areas": ", ".join(d.get("focus_areas") or []),
        "talent_grid_label": tg.get("label"),
        "talent_grid_cell": tg.get("cell"),
        "performance_score": tg.get("performance_score"),
        "quality_score": tg.get("quality_score"),
    }


@router.get("/api/periods/{period_id}/export")
def export_period_solvers(period_id: int, fmt: str = "csv", db: Session = Depends(get_db),
                           _: str = Depends(auth.require_admin)):
    period = db.get(models.Period, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    rows = [_flatten_snapshot_row(s.name, snapshot_to_dict(s)) for s in period.snapshots]
    rows.sort(key=lambda r: r["solver_name"])
    return _export_response(rows, fmt, f"solvers_{period.label.replace(' ', '_')}")


@router.get("/api/fleet-bonus/export")
def export_fleet_bonus(fmt: str = "csv", db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    entries = db.scalars(select(models.FleetEntry).order_by(models.FleetEntry.created_at.asc())).all()
    rows = [
        {
            "fleet_client_name": e.fleet_client_name, "solver_name": e.solver_name,
            "fleet_size_booked": e.fleet_size_booked, "price_per_vehicle": e.price_per_vehicle,
            "approved_count": e.approved_count, "client_satisfied": "Yes" if e.client_satisfied else "No",
            "rate": e.rate, "billed_amount": e.billed_amount, "bonus_amount": e.bonus_amount,
            "status": e.status, "notes": e.notes,
        }
        for e in entries
    ]
    return _export_response(rows, fmt, "fleet_bonus")


@router.get("/api/complaints/export")
def export_complaints(fmt: str = "csv", db: Session = Depends(get_db), _: str = Depends(auth.require_admin)):
    rows = [_complaint_to_dict(c) for c in db.scalars(select(models.Complaint)).all()]
    return _export_response(rows, fmt, "complaints")


# ---------------------------------------------------------------------------
# Solver details import / export (contact list)
# ---------------------------------------------------------------------------

_DETAIL_HEADERS = {
    "name": ["name", "solver", "solver name", "solver_name", "full name"],
    "email": ["email", "e-mail", "email address"],
    "phone": ["phone", "phone number", "mobile", "contact", "contacts", "tel", "telephone"],
    "region": ["region", "area", "zone"],
    "location": ["location", "town", "base"],
    "active": ["active", "status"],
}


def _find_cols(headers: list[str]) -> dict:
    out = {}
    low = [h.strip().lower() for h in headers]
    for key, aliases in _DETAIL_HEADERS.items():
        for i, h in enumerate(low):
            if h in aliases:
                out[key] = i
                break
    return out


@router.post("/api/registered-solvers/import-details")
async def import_solver_details(file: UploadFile = File(...), db: Session = Depends(get_db),
                                _: str = Depends(auth.require_admin)):
    """Upload an updated solver list with contact details. One header row;
    columns recognised (any order, case-insensitive): Name, Email, Phone/
    Mobile/Contact, Region, Location, Active/Status. Only Name is required.
    Existing solvers (matched by name, ignoring case/extra spaces) are
    updated; blank cells never overwrite existing data. New solvers need a
    Region. Emails also feed the coaching/broadcast email list."""
    if not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Upload an .xlsx or .csv file")
    content = await file.read()
    try:
        df = (pd.read_csv(io.BytesIO(content), dtype=str) if file.filename.lower().endswith(".csv")
              else pd.read_excel(io.BytesIO(content), dtype=str))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {e}")
    cols = _find_cols([str(c) for c in df.columns])
    if "name" not in cols:
        raise HTTPException(status_code=400, detail="No 'Name' column found in the header row")

    def cell(row, key):
        if key not in cols:
            return None
        v = row.iloc[cols[key]]
        if pd.isna(v):
            return None
        v = str(v).strip()
        return v or None

    def norm(n): return " ".join(n.lower().split())
    solvers = {norm(s.name): s for s in db.scalars(select(models.Solver)).all()}
    emails = {norm(e.name): e for e in db.scalars(select(models.SolverEmail)).all()}

    added = updated = emails_set = 0
    problems = []
    for idx, row in df.iterrows():
        name = cell(row, "name")
        if not name:
            continue
        name = " ".join(name.lstrip("⭐ ").split())
        email, phone = cell(row, "email"), cell(row, "phone")
        region, location, active = cell(row, "region"), cell(row, "location"), cell(row, "active")
        if email and ("@" not in email or "." not in email.split("@")[-1]):
            problems.append(f"Row {idx + 2}: '{email}' is not a valid email, skipped for {name}")
            email = None
        act = None
        if active:
            act = 0 if active.lower() in ("0", "no", "inactive", "left", "archived", "false") else 1

        s = solvers.get(norm(name))
        if s:
            if region: s.region = region
            if location: s.location = location
            if phone: s.phone = phone
            if act is not None: s.active = act
            updated += 1
        else:
            if not region:
                problems.append(f"Row {idx + 2}: {name} is new but has no Region, skipped")
                continue
            s = models.Solver(name=name, region=region, location=location or "", phone=phone,
                              active=1 if act is None else act, is_lead=0)
            db.add(s)
            solvers[norm(name)] = s
            added += 1
        if email:
            e = emails.get(norm(s.name))
            if e:
                e.email = email
            else:
                e = models.SolverEmail(name=s.name, email=email)
                db.add(e)
                emails[norm(s.name)] = e
            emails_set += 1
    db.commit()
    return {"ok": True, "added": added, "updated": updated, "emails_set": emails_set, "problems": problems}


@router.get("/api/registered-solvers/export")
def export_solver_details(fmt: str = "xlsx", db: Session = Depends(get_db),
                          _: str = Depends(auth.require_admin)):
    """Current roster with contact details — also the template to edit and re-upload."""
    emails = {e.name: e.email for e in db.scalars(select(models.SolverEmail)).all()}
    rows = [{"Name": s.name, "Email": emails.get(s.name, ""), "Phone": s.phone or "",
             "Region": s.region, "Location": s.location or "",
             "Active": "Yes" if s.active else "No"}
            for s in db.scalars(select(models.Solver).order_by(models.Solver.region, models.Solver.name)).all()]
    if not rows:
        rows = [{"Name": "", "Email": "", "Phone": "", "Region": "", "Location": "", "Active": ""}]
    return _export_response(rows, fmt, "solver_details")
