"""Analyse a Zoho-exported solver-performance workbook.

This is functionally identical to scripts/analyse.py from the standalone skill —
same TARGETS, same metric definitions, same classifications — but returns
dictionaries shaped for direct insertion into the SolverSnapshot ORM model.

The submission rate definition (the source of truth):
    submission_rate = (rows in TOTAL VALUED for this solver)
                     / ((rows in TOTAL VALUED) + (rows in PENDING REASONS))

All other metrics follow the same definitions as the skill.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import pandas as pd


TARGETS = {
    "response_tat_hours_max": 10.0,
    "onsite_tat_hours_max": 0.5,
    "rating_min": 4.5,
    "volume_min_monthly": 60,
    "submission_rate_min": 0.85,
    "approval_rate_min": 0.95,
    "min_ratings_for_judgement": 3,
    "stuck_job_hours": 72.0,
}


# ---------------------------------------------------------------------------
# Classification — same rules as the skill
# ---------------------------------------------------------------------------

def classify_solver(stats: dict, team: dict) -> dict:
    """Classify each metric vs the TARGET only — never against the team.

    Each metric gets one of: "strong", "on_track", "needs_work", "insufficient_data".

    Rules (all purely target-based):
      - "strong"      = comfortably past target (target × 0.5 for "lower is better",
                        target × 2 or a defined high bar for "higher is better")
      - "on_track"    = inside target zone but not "strong"
      - "needs_work"  = past target × 1.5 ("lower is better") or below target × 0.75
                        ("higher is better") — i.e. clearly outside target
      - "insufficient_data" = not enough rows to judge fairly

    Note: `team` is still passed in (so the signature is unchanged for callers),
    but it is intentionally NOT consulted in this function. It remains available
    on snapshots and in the UI for *context* (the team marker on scorecard bars,
    the "team median X" footnotes) — but never to judge a solver.
    """
    out = {}

    # ---- Response TAT / full basket journey (lower is better, target ≤ 10h) ----
    rtat_med = stats.get("median_response_tat_hrs")
    if rtat_med is None or pd.isna(rtat_med):
        out["response_tat"] = "insufficient_data"
    elif rtat_med <= TARGETS["response_tat_hours_max"] * 0.5:   # ≤ 5h
        out["response_tat"] = "strong"
    elif rtat_med <= TARGETS["response_tat_hours_max"]:          # ≤ 10h
        out["response_tat"] = "on_track"
    elif rtat_med <= TARGETS["response_tat_hours_max"] * 1.5:   # ≤ 15h — close but over
        out["response_tat"] = "on_track"
    else:
        out["response_tat"] = "needs_work"

    # ---- On-site TAT (lower is better, target ≤ 30 min) ----
    otat = stats.get("avg_onsite_tat_hrs")
    if otat is None or pd.isna(otat):
        out["onsite_tat"] = "insufficient_data"
    elif otat <= TARGETS["onsite_tat_hours_max"] * 0.5:
        out["onsite_tat"] = "strong"
    elif otat <= TARGETS["onsite_tat_hours_max"]:
        out["onsite_tat"] = "on_track"
    elif otat <= TARGETS["onsite_tat_hours_max"] * 1.5:
        out["onsite_tat"] = "on_track"
    else:
        out["onsite_tat"] = "needs_work"

    # ---- Client rating (higher is better, target ≥ 4.5) ----
    n = stats.get("n_ratings", 0)
    rating = stats.get("avg_rating")
    if n < TARGETS["min_ratings_for_judgement"] or rating is None or pd.isna(rating):
        out["rating"] = "insufficient_data"
    elif rating >= 4.8:
        out["rating"] = "strong"
    elif rating >= TARGETS["rating_min"]:
        out["rating"] = "on_track"
    elif rating >= TARGETS["rating_min"] * 0.75:   # >= 3.375 with default 4.5 target
        out["rating"] = "on_track"
    else:
        out["rating"] = "needs_work"

    # ---- Volume (higher is better, target ≥ 60/month) ----
    vol = stats.get("volume", 0)
    if vol >= TARGETS["volume_min_monthly"] * 2:
        out["volume"] = "strong"
    elif vol >= TARGETS["volume_min_monthly"]:
        out["volume"] = "on_track"
    elif vol >= TARGETS["volume_min_monthly"] * 0.5:
        out["volume"] = "on_track"
    else:
        out["volume"] = "needs_work"

    # ---- Submission rate (higher is better, target ≥ 85%) ----
    sub = stats.get("submission_rate")
    if sub is None or pd.isna(sub) or stats.get("assigned_count", 0) < 5:
        out["submission_rate"] = "insufficient_data"
    elif sub >= 0.95:
        out["submission_rate"] = "strong"
    elif sub >= TARGETS["submission_rate_min"]:
        out["submission_rate"] = "on_track"
    elif sub >= 0.70:
        out["submission_rate"] = "on_track"
    else:
        out["submission_rate"] = "needs_work"

    return out


def pick_training_modules(classifications: dict) -> list[str]:
    needs = {a for a, s in classifications.items() if s == "needs_work"}
    time_flagged = needs & {"response_tat", "onsite_tat"}
    submission_flagged = needs & {"submission_rate"}
    picked = []
    if time_flagged:
        picked.append("time")
    if submission_flagged:
        picked.append("submission")
    if not picked and not needs:
        picked = ["strong"]
    return picked


def infer_focus_areas(classifications: dict) -> list[str]:
    """Higher-level focus tags for the UI filter pills."""
    areas = []
    if classifications.get("response_tat") == "needs_work" or \
       classifications.get("onsite_tat") == "needs_work":
        areas.append("time")
    if classifications.get("submission_rate") == "needs_work":
        areas.append("submission")
    if classifications.get("rating") == "needs_work":
        areas.append("rating")
    if not areas and all(
        v in ("strong", "on_track", "insufficient_data") for v in classifications.values()
    ):
        areas.append("strong")
    return areas


# ---------------------------------------------------------------------------
# Main: analyse a workbook
# ---------------------------------------------------------------------------

REQUIRED_SHEETS = ["TOTAL VALUED", "CLIENT RATING", "PENDING REASONS"]


def analyse_workbook(input_path: Path) -> dict[str, Any]:
    """Return {'team': {...}, 'solvers': [{...}, ...]} ready for DB insertion."""
    # Validate sheets up front so we can return a clear error
    wb = pd.ExcelFile(input_path)
    missing = [s for s in REQUIRED_SHEETS if s not in wb.sheet_names]
    if missing:
        raise ValueError(f"Workbook missing required sheets: {', '.join(missing)}")

    tv = pd.read_excel(input_path, sheet_name="TOTAL VALUED")
    cr = pd.read_excel(input_path, sheet_name="CLIENT RATING")
    pr = pd.read_excel(input_path, sheet_name="PENDING REASONS")

    # Required columns
    for col in ("Valuation_Start", "Valuation_Date", "Schedule_date",
                "Requested_Date", "Approval_Status", "Solver"):
        if col not in tv.columns:
            raise ValueError(f"TOTAL VALUED missing column: {col}")
    for col in ("Solver", "rating"):
        if col not in cr.columns:
            raise ValueError(f"CLIENT RATING missing column: {col}")
    for col in ("solver_name", "latest_pending_reason"):
        if col not in pr.columns:
            raise ValueError(f"PENDING REASONS missing column: {col}")

    # TATs
    tv["_schedule_or_request"] = tv["Schedule_date"].fillna(tv["Requested_Date"])
    tv["response_tat_hrs"] = (
        tv["Valuation_Date"] - tv["_schedule_or_request"]
    ).dt.total_seconds() / 3600
    tv["onsite_tat_hrs"] = (
        tv["Valuation_Date"] - tv["Valuation_Start"]
    ).dt.total_seconds() / 3600

    approved = tv[tv["Approval_Status"] == "Approved"].copy()
    approved = approved[(approved["onsite_tat_hrs"] >= 0) & (approved["onsite_tat_hrs"] < 24)]
    approved = approved[approved["response_tat_hrs"] >= 0]

    # Per-solver aggregations
    stuck_threshold = TARGETS["stuck_job_hours"]
    per_solver = approved.groupby("Solver").agg(
        volume=("Vehicle_reg", "count"),
        avg_response_tat_hrs=("response_tat_hrs", "mean"),
        median_response_tat_hrs=("response_tat_hrs", "median"),
        avg_onsite_tat_hrs=("onsite_tat_hrs", "mean"),
        median_onsite_tat_hrs=("onsite_tat_hrs", "median"),
    )
    stuck = approved.assign(stuck=approved["response_tat_hrs"] > stuck_threshold) \
        .groupby("Solver")["stuck"].agg(["sum", "count"])
    stuck.columns = ["stuck_job_count", "_t"]
    stuck["stuck_job_rate"] = stuck["stuck_job_count"] / stuck["_t"]
    stuck = stuck.drop(columns=["_t"])
    per_solver = per_solver.join(stuck)

    # Approval rate (across all attempts in TOTAL VALUED, regardless of status)
    attempts = tv[tv["Solver"].notna()]
    approval = attempts.groupby("Solver").agg(
        total_attempts=("Approval_Status", "count"),
        approved_count=("Approval_Status", lambda s: (s == "Approved").sum()),
    )
    approval["approval_rate"] = approval["approved_count"] / approval["total_attempts"]

    # Valued count per solver (every TV row counts as a submission)
    valued = tv[tv["Solver"].notna()].groupby("Solver").size().rename("valued_count")

    # Ratings
    ratings_summary = cr.groupby("Solver").agg(
        avg_rating=("rating", "mean"),
        n_ratings=("rating", "count"),
    )
    sub_cols = [c for c in ("presentation_rating", "professionalism_rating", "punctuality_rating") if c in cr.columns]
    if sub_cols:
        sub_means = cr.groupby("Solver")[sub_cols].mean()
        ratings_summary = ratings_summary.join(sub_means)

    # Jobs initiated by each solver — where the solver brought in the work themselves
    # (Initiator_Source == "Solver" AND Initiated_by matches the solver name).
    # This is a separate metric from Solver (who completed the valuation).
    if "Initiator_Source" in tv.columns and "Initiated_by" in tv.columns:
        solver_initiated = tv[
            (tv["Initiator_Source"] == "Solver") &
            (tv["Initiated_by"].notna())
        ].groupby("Initiated_by").size().rename("jobs_initiated")
    else:
        solver_initiated = pd.Series(dtype=int, name="jobs_initiated")

    # Pending
    pending_total = pr.groupby("solver_name").size().rename("pending_count")
    pending_reasons = pr.groupby(["solver_name", "latest_pending_reason"]).size().unstack(fill_value=0)

    # Merge — outer everywhere so we don't drop any solver
    summary = per_solver.join(approval[["total_attempts", "approval_rate"]], how="outer")
    summary = summary.join(valued, how="outer")
    summary = summary.join(ratings_summary, how="outer")
    summary = summary.join(pending_total, how="outer")
    summary = summary.join(solver_initiated, how="outer")

    summary["volume"] = summary["volume"].fillna(0).astype(int)
    summary["total_attempts"] = summary["total_attempts"].fillna(0).astype(int)
    summary["valued_count"] = summary["valued_count"].fillna(0).astype(int)
    summary["n_ratings"] = summary["n_ratings"].fillna(0).astype(int)
    summary["pending_count"] = summary["pending_count"].fillna(0).astype(int)
    summary["stuck_job_count"] = summary["stuck_job_count"].fillna(0).astype(int)
    summary["jobs_initiated"] = summary["jobs_initiated"].fillna(0).astype(int)
    summary["assigned_count"] = summary["valued_count"] + summary["pending_count"]
    summary["submission_rate"] = summary.apply(
        lambda r: r["valued_count"] / r["assigned_count"] if r["assigned_count"] > 0 else float("nan"),
        axis=1,
    )

    # Team stats
    total_valued = int(summary["valued_count"].sum())
    total_pending = int(summary["pending_count"].sum())
    total_assigned = total_valued + total_pending
    total_jobs_initiated = int(summary["jobs_initiated"].sum())
    team_submission_rate = (total_valued / total_assigned) if total_assigned else None

    team = {
        "avg_response_tat_hrs": float(approved["response_tat_hrs"].mean()) if len(approved) else None,
        "median_response_tat_hrs": float(approved["response_tat_hrs"].median()) if len(approved) else None,
        "avg_onsite_tat_hrs": float(approved["onsite_tat_hrs"].mean()) if len(approved) else None,
        "median_onsite_tat_hrs": float(approved["onsite_tat_hrs"].median()) if len(approved) else None,
        "avg_rating": float(cr["rating"].mean()) if len(cr) else None,
        "median_rating": float(cr["rating"].median()) if len(cr) else None,
        "avg_volume": float(summary["volume"].mean()),
        "median_volume": float(summary["volume"].median()),
        "team_submission_rate": team_submission_rate,
        "median_submission_rate": float(summary["submission_rate"].dropna().median()) if summary["submission_rate"].notna().any() else None,
        "total_solvers": int(len(summary)),
        "total_valuations": int(len(approved)),
        "total_jobs_assigned": total_assigned,
        "total_jobs_pending": total_pending,
        "total_jobs_initiated_by_solvers": total_jobs_initiated,
        "pct_stuck_jobs_team": float((approved["response_tat_hrs"] > stuck_threshold).mean()) if len(approved) else 0.0,
    }

    # Per-solver records
    solvers = []
    for solver_name, row in summary.iterrows():
        if pd.isna(solver_name) or not str(solver_name).strip():
            continue

        def _f(v):
            if v is None: return None
            if pd.isna(v): return None
            return float(v)

        stats = {
            "name": str(solver_name),
            "volume": int(row["volume"]),
            "total_attempts": int(row["total_attempts"]),
            "valued_count": int(row["valued_count"]),
            "assigned_count": int(row["assigned_count"]),
            "jobs_initiated": int(row["jobs_initiated"]),
            "submission_rate": _f(row.get("submission_rate")),
            "avg_response_tat_hrs": _f(row.get("avg_response_tat_hrs")),
            "median_response_tat_hrs": _f(row.get("median_response_tat_hrs")),
            "avg_onsite_tat_hrs": _f(row.get("avg_onsite_tat_hrs")),
            "median_onsite_tat_hrs": _f(row.get("median_onsite_tat_hrs")),
            "stuck_job_count": int(row["stuck_job_count"]),
            "stuck_job_rate": _f(row.get("stuck_job_rate")),
            "avg_rating": _f(row.get("avg_rating")),
            "n_ratings": int(row["n_ratings"]),
            "approval_rate": _f(row.get("approval_rate")),
            "pending_count": int(row["pending_count"]),
        }

        # Sub-ratings into extra
        extra = {}
        for c in sub_cols:
            extra[c] = _f(row.get(c))

        # Top pending reasons
        if solver_name in pending_reasons.index:
            reasons = pending_reasons.loc[solver_name].sort_values(ascending=False)
            top_reasons = [(reason, int(count)) for reason, count in reasons.items() if count > 0][:3]
        else:
            top_reasons = []
        extra["top_pending_reasons"] = top_reasons

        stats["extra"] = extra
        stats["classifications"] = classify_solver(stats, team)
        stats["training_modules"] = pick_training_modules(stats["classifications"])
        stats["focus_areas"] = infer_focus_areas(stats["classifications"])
        solvers.append(stats)

    solvers.sort(key=lambda s: s["volume"], reverse=True)

    return {"team": team, "solvers": solvers, "targets": TARGETS}
