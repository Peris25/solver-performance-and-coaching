"""Database models.

Two main tables for now:

- Period: one row per uploaded workbook. The "label" is human (e.g. "April 2026").
- SolverSnapshot: one row per solver per period — the computed metrics.

History queries are just SELECTs across multiple periods for the same solver name.
A future User table can be added without disturbing these.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from datetime import datetime

from app.database import Base


class Period(Base):
    __tablename__ = "periods"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(64), nullable=False, index=True, unique=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    uploaded_filename = Column(String(255), nullable=True)

    # Team-level metrics for the dashboard (denormalized so the dashboard
    # endpoint doesn't have to re-aggregate every time).
    total_solvers = Column(Integer, default=0)
    total_valuations = Column(Integer, default=0)
    total_jobs_assigned = Column(Integer, default=0)
    total_jobs_pending = Column(Integer, default=0)
    total_jobs_initiated_by_solvers = Column(Integer, default=0)
    team_submission_rate = Column(Float, nullable=True)
    median_submission_rate = Column(Float, nullable=True)
    avg_total_tat_hrs = Column(Float, nullable=True)
    median_total_tat_hrs = Column(Float, nullable=True)
    avg_response_tat_hrs = Column(Float, nullable=True)
    median_response_tat_hrs = Column(Float, nullable=True)
    avg_onsite_tat_hrs = Column(Float, nullable=True)
    median_onsite_tat_hrs = Column(Float, nullable=True)
    avg_rating = Column(Float, nullable=True)
    avg_volume = Column(Float, nullable=True)
    median_volume = Column(Float, nullable=True)
    pct_stuck_jobs_team = Column(Float, nullable=True)

    # Backlog: jobs initiated before this period, cleared during it.
    # period_start/end are derived from the data itself (min/max
    # Valuation_Date in TOTAL VALUED), not a separately-entered date range.
    backlog_period_start = Column(DateTime, nullable=True)
    backlog_period_end = Column(DateTime, nullable=True)
    total_backlog = Column(Integer, default=0)
    total_valued_this_period = Column(Integer, default=0)
    pct_backlog = Column(Float, nullable=True)
    oldest_backlog_days = Column(Float, nullable=True)

    snapshots = relationship("SolverSnapshot", back_populates="period",
                             cascade="all, delete-orphan")


class SolverSnapshot(Base):
    __tablename__ = "solver_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    period_id = Column(Integer, ForeignKey("periods.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False, index=True)

    # Volumes / counts
    volume = Column(Integer, default=0)
    total_attempts = Column(Integer, default=0)
    valued_count = Column(Integer, default=0)
    assigned_count = Column(Integer, default=0)
    pending_count = Column(Integer, default=0)
    jobs_initiated = Column(Integer, default=0)
    stuck_job_count = Column(Integer, default=0)
    n_ratings = Column(Integer, default=0)
    backlog_count = Column(Integer, default=0)

    # Computed rates / averages
    submission_rate = Column(Float, nullable=True)
    approval_rate = Column(Float, nullable=True)
    avg_total_tat_hrs = Column(Float, nullable=True)
    median_total_tat_hrs = Column(Float, nullable=True)
    avg_response_tat_hrs = Column(Float, nullable=True)
    median_response_tat_hrs = Column(Float, nullable=True)
    avg_onsite_tat_hrs = Column(Float, nullable=True)
    median_onsite_tat_hrs = Column(Float, nullable=True)
    avg_rating = Column(Float, nullable=True)
    stuck_job_rate = Column(Float, nullable=True)
    avg_backlog_age_days = Column(Float, nullable=True)

    # Classifications and training picks (JSON for forward-compat)
    classifications = Column(JSON, nullable=False, default=dict)
    training_modules = Column(JSON, nullable=False, default=list)
    focus_areas = Column(JSON, nullable=False, default=list)

    # Raw extras (top pending reasons, sub-ratings) kept as JSON so we
    # don't have to migrate the schema for every minor new field.
    extra = Column(JSON, nullable=False, default=dict)

    period = relationship("Period", back_populates="snapshots")


class SolverEmail(Base):
    """Solver name → email address mapping.

    Emails aren't in the Zoho export, so the admin manages this directly through
    the portal. Required before automated coaching emails can be sent.
    """
    __tablename__ = "solver_emails"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        nullable=False)


class EmailSendLog(Base):
    """Audit log of every coaching email sent.

    Records when, to whom, for which period, the reason, and the outcome.
    Used to (a) prevent accidental duplicate sends and (b) show the admin
    a history of what was sent.
    """
    __tablename__ = "email_send_logs"

    id = Column(Integer, primary_key=True, index=True)
    # Broadcast sends aren't tied to a period, so this may be null.
    period_id = Column(Integer, ForeignKey("periods.id"), nullable=True, index=True)
    solver_name = Column(String(128), nullable=False, index=True)
    email_to = Column(String(255), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String(32), nullable=False, default="sent")  # "sent" | "failed" | "skipped"
    error = Column(Text, nullable=True)
    trigger = Column(String(32), nullable=False, default="manual")  # "manual" | "bulk_targets_missed" | "broadcast"
    focus_areas = Column(JSON, nullable=False, default=list)  # snapshot of why they were emailed
    subject = Column(String(255), nullable=True)
    attachments = Column(JSON, nullable=False, default=list)

    period = relationship("Period")


class Solver(Base):
    """Registered solver — name, region, location, active flag.

    This is the canonical list of solvers (loaded from SOLVERS_REGIONAL_LIST.xlsx
    on first run, then managed by the admin through the UI).

    Snapshot data (from Zoho uploads) gets matched to these registered solvers
    by name to enable regional analysis.
    """
    __tablename__ = "solvers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    region = Column(String(64), nullable=False, index=True)
    location = Column(String(128), nullable=True)
    phone = Column(String(64), nullable=True)
    active = Column(Integer, default=1, nullable=False)  # 1 = active, 0 = archived
    is_lead = Column(Integer, default=0, nullable=False)  # 1 = regional lead
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        nullable=False)


class RegionSnapshot(Base):
    """Per-region aggregation for a period. Cached so the dashboard doesn't
    re-aggregate on every request."""
    __tablename__ = "region_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    period_id = Column(Integer, ForeignKey("periods.id"), nullable=False, index=True)
    region = Column(String(64), nullable=False, index=True)

    solver_count = Column(Integer, default=0)
    total_assigned = Column(Integer, default=0)
    total_valued = Column(Integer, default=0)
    total_pending = Column(Integer, default=0)
    total_jobs_initiated = Column(Integer, default=0)
    jobs_per_solver = Column(Float, nullable=True)
    submission_rate = Column(Float, nullable=True)
    avg_total_tat_hrs = Column(Float, nullable=True)
    avg_response_tat_hrs = Column(Float, nullable=True)
    avg_onsite_tat_hrs = Column(Float, nullable=True)
    avg_rating = Column(Float, nullable=True)
    avg_jobs_initiated = Column(Float, nullable=True)
    staffing = Column(String(32), nullable=True)        # "overloaded" | "balanced" | "under_utilised"
    performance = Column(String(32), nullable=True)     # "strong" | "on_track" | "needs_improvement" | "insufficient_data"
    needs_coaching_count = Column(Integer, default=0)
    strong_count = Column(Integer, default=0)
    locations = Column(JSON, nullable=False, default=list)
    solver_names = Column(JSON, nullable=False, default=list)

    period = relationship("Period")


class JobRecord(Base):
    """One row per job as it appears in TOTAL VALUED or SOLVERS BASKET.

    This is the raw, row-level data behind the period-aggregated
    SolverSnapshot — kept so the talent grid (and, later, any other view)
    can be recomputed over an arbitrary date range instead of being locked
    to whichever monthly/weekly file it happened to arrive in. A job
    completed July 3rd counts toward a "July 1–15" query regardless of
    which upload it came from.

    `sheet_source` distinguishes which sheet a row came from, since TOTAL
    VALUED and SOLVERS BASKET share an identical column layout in the Zoho
    export but represent different things: TOTAL VALUED is "valuations
    actually completed", SOLVERS BASKET is "jobs handed to a solver this
    period, whatever the outcome" (used for submission rate / pending).

    `period_id` records which upload the row arrived with, for traceability
    only — it is NOT used to scope date-range queries (those filter on the
    row's own dates). A (sheet_source, vehicle_reg, requested_date, solver)
    row is only inserted once even if the same job appears in an overlapping
    later upload, so re-uploading an overlapping period doesn't double-count.
    """
    __tablename__ = "job_records"

    id = Column(Integer, primary_key=True, index=True)
    period_id = Column(Integer, ForeignKey("periods.id"), nullable=True, index=True)
    sheet_source = Column(String(16), nullable=False, index=True)  # "total_valued" | "solvers_basket"

    solver = Column(String(128), nullable=False, index=True)
    vehicle_reg = Column(String(64), nullable=True, index=True)

    requested_date = Column(DateTime, nullable=True, index=True)
    schedule_date = Column(DateTime, nullable=True)
    valuation_start = Column(DateTime, nullable=True)
    valuation_date = Column(DateTime, nullable=True, index=True)

    request_status = Column(String(64), nullable=True)     # basket: "Completed" | "Solver accept" | ...
    approval_status = Column(String(64), nullable=True)     # "Approved" | ...
    initiator_source = Column(String(32), nullable=True)    # "Solver" | ...
    initiated_by = Column(String(128), nullable=True)

    dedup_key = Column(String(255), nullable=False, unique=True, index=True)

    period = relationship("Period")


class AppSetting(Base):
    """Single-row-per-key admin-editable settings (fleet bonus rate, TAT
    allowance for remote regions, dormancy threshold, etc.) so these can be
    tuned from the UI without a code change/redeploy."""
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        nullable=False)


class FleetEntry(Base):
    """One sourced fleet, mirroring SOLVERS__Fleet_Bonus_Calculator.xlsx.

    Bonus = billed_amount x rate, where rate is the flat bonus rate
    (default 3%) if fleet_size_booked >= min_fleet_size AND
    client_satisfied is true, else 0. billed_amount = price_per_vehicle x
    approved_count. See /api/settings/fleet-bonus for the editable rate
    and minimum.
    """
    __tablename__ = "fleet_entries"

    id = Column(Integer, primary_key=True, index=True)
    fleet_client_name = Column(String(255), nullable=False)
    solver_name = Column(String(128), nullable=False, index=True)
    fleet_size_booked = Column(Integer, nullable=False, default=0)
    price_per_vehicle = Column(Float, nullable=False, default=0.0)
    approved_count = Column(Integer, nullable=False, default=0)
    client_satisfied = Column(Integer, default=0, nullable=False)  # 1 = Yes / closed, 0 = not yet
    rate = Column(Float, nullable=True)
    billed_amount = Column(Float, nullable=True)
    bonus_amount = Column(Float, nullable=True)
    status = Column(String(32), nullable=False, default="open")  # "open" | "closed" | "paid"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        nullable=False)


class Complaint(Base):
    """A complaint or accident logged against a solver's operations.

    `kind` distinguishes a client/operational complaint from a field
    accident (e.g. vehicle collision while travelling to a job). Free-text
    `description`/`resolution_notes` plus a small status workflow is
    deliberately simple — this is a log, not a full case-management tool.
    """
    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(16), nullable=False, default="complaint")  # "complaint" | "accident"
    solver_name = Column(String(128), nullable=False, index=True)
    region = Column(String(64), nullable=True)
    date_occurred = Column(DateTime, nullable=True)
    severity = Column(String(16), nullable=False, default="medium")  # "low" | "medium" | "high"
    description = Column(Text, nullable=False)
    reported_by = Column(String(128), nullable=True)
    status = Column(String(16), nullable=False, default="open")  # "open" | "investigating" | "resolved" | "closed"
    resolution_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        nullable=False)


class RatingRecord(Base):
    """One row per client rating, from the CLIENT RATING sheet.

    `initiated_date` is the best available date anchor for range queries —
    the sheet doesn't carry a separate "date rated" column, so filtering
    ratings by the underlying job's initiated date is the closest available
    proxy for "ratings from jobs that happened in this window."
    """
    __tablename__ = "rating_records"

    id = Column(Integer, primary_key=True, index=True)
    period_id = Column(Integer, ForeignKey("periods.id"), nullable=True, index=True)

    solver = Column(String(128), nullable=False, index=True)
    vehicle_reg = Column(String(64), nullable=True)
    initiated_date = Column(DateTime, nullable=True, index=True)

    rating = Column(Float, nullable=True)
    presentation_rating = Column(Float, nullable=True)
    professionalism_rating = Column(Float, nullable=True)
    punctuality_rating = Column(Float, nullable=True)

    dedup_key = Column(String(255), nullable=False, unique=True, index=True)

    period = relationship("Period")
