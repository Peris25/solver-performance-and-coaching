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
    period_id = Column(Integer, ForeignKey("periods.id"), nullable=False, index=True)
    solver_name = Column(String(128), nullable=False, index=True)
    email_to = Column(String(255), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String(32), nullable=False, default="sent")  # "sent" | "failed" | "skipped"
    error = Column(Text, nullable=True)
    trigger = Column(String(32), nullable=False, default="manual")  # "manual" | "bulk_targets_missed"
    focus_areas = Column(JSON, nullable=False, default=list)  # snapshot of why they were emailed

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
    avg_response_tat_hrs = Column(Float, nullable=True)
    avg_onsite_tat_hrs = Column(Float, nullable=True)
    avg_rating = Column(Float, nullable=True)
    staffing = Column(String(32), nullable=True)        # "overloaded" | "balanced" | "under_utilised"
    performance = Column(String(32), nullable=True)     # "strong" | "on_track" | "needs_improvement" | "insufficient_data"
    needs_coaching_count = Column(Integer, default=0)
    strong_count = Column(Integer, default=0)
    locations = Column(JSON, nullable=False, default=list)
    solver_names = Column(JSON, nullable=False, default=list)

    period = relationship("Period")
