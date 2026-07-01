"""Analyse a Zoho-exported solver-performance workbook.

The workbook has THREE sheets:

  TOTAL VALUED   = jobs the solver has completed (valued or approved).
                   Source of TAT and quality metrics. Only contains finished work.
  SOLVERS BASKET = ALL jobs assigned to the solver during the period (pending
                   + completed). Source of assigned/pending counts and the
                   submission rate (completed / total in basket).
  CLIENT RATING  = client feedback. Ratings of 0 are bogus (no actual review
                   submitted) and are excluded; only ratings 1-5 count.

This module reads the workbook and returns a fully shaped dict ready for
DB insertion. Classification is purely target-based (never team-relative).
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Optional
import re

import pandas as pd


# ---------------------------------------------------------------------------
# TARGETS — the single source of truth for "did this solver meet the bar?"
# ---------------------------------------------------------------------------
TARGETS = {
    "total_tat_hours_max": 10.0,       # assignment -> submission, target ≤ 10h (primary metric)
    "response_tat_hours_max": 9.5,     # schedule -> valuation, target ≤ 9h 30min (sub-component)
    "onsite_tat_hours_max": 0.5,       # 30 minutes on-site (sub-component)
    "rating_min": 4.5,                  # client rating ≥ 4.5/5
    "volume_min_monthly": 60,           # at least 60 valuations/month
    "submission_rate_min": 0.85,        # 85% of assigned closed as valued
    "reject_rate_max": 0.05,            # ≤ 5% of submitted reports rejected (when data available)
    "min_ratings_for_judgement": 3,     # need at least 3 ratings to judge
    "stuck_job_hours": 72.0,            # any job > 72h since assigned is "stuck"
    # Region staffing thresholds (jobs/solver/month)
    "regional_overload_jobs_per_solver": 90,   # > 90 jobs/solver = overloaded
    "regional_under_jobs_per_solver": 30,      # < 30 = under-utilised
}


# ---------------------------------------------------------------------------
# Classification — target-only, no team comparison
# ---------------------------------------------------------------------------

def classify_solver(stats: dict) -> dict:
    """Each metric -> 'strong' | 'on_track' | 'needs_work' | 'insufficient_data'.

    `total_tat` (assignment -> submission, average) is the primary TAT signal,
    judged against 10h target. `response_tat` and `onsite_tat` are kept as
    breakdown sub-components shown in the coaching doc.
    """
    out = {}

    # PRIMARY: total TAT (assigned -> submitted), average
    total_tat = stats.get("avg_total_tat_hrs")
    if total_tat is None or pd.isna(total_tat):
        out["total_tat"] = "insufficient_data"
    elif total_tat <= TARGETS["total_tat_hours_max"] * 0.5:
        out["total_tat"] = "strong"
    elif total_tat <= TARGETS["total_tat_hours_max"]:
        out["total_tat"] = "on_track"
    elif total_tat <= TARGETS["total_tat_hours_max"] * 1.5:
        out["total_tat"] = "on_track"
    else:
        out["total_tat"] = "needs_work"

    # Sub-component: response TAT (schedule -> submitted)
    rtat = stats.get("avg_response_tat_hrs")
    if rtat is None or pd.isna(rtat):
        out["response_tat"] = "insufficient_data"
    elif rtat <= TARGETS["response_tat_hours_max"] * 0.5:
        out["response_tat"] = "strong"
    elif rtat <= TARGETS["response_tat_hours_max"]:
        out["response_tat"] = "on_track"
    elif rtat <= TARGETS["response_tat_hours_max"] * 1.5:
        out["response_tat"] = "on_track"
    else:
        out["response_tat"] = "needs_work"

    # Sub-component: on-site TAT
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

    n = stats.get("n_ratings", 0)
    rating = stats.get("avg_rating")
    if n < TARGETS["min_ratings_for_judgement"] or rating is None or pd.isna(rating):
        out["rating"] = "insufficient_data"
    elif rating >= 4.8:
        out["rating"] = "strong"
    elif rating >= TARGETS["rating_min"]:
        out["rating"] = "on_track"
    elif rating >= TARGETS["rating_min"] * 0.75:
        out["rating"] = "on_track"
    else:
        out["rating"] = "needs_work"

    vol = stats.get("volume", 0)
    if vol >= TARGETS["volume_min_monthly"] * 2:
        out["volume"] = "strong"
    elif vol >= TARGETS["volume_min_monthly"]:
        out["volume"] = "on_track"
    elif vol >= TARGETS["volume_min_monthly"] * 0.5:
        out["volume"] = "on_track"
    else:
        out["volume"] = "needs_work"

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
    picked = []
    if needs & {"total_tat", "response_tat", "onsite_tat"}:
        picked.append("time")
    if "submission_rate" in needs:
        picked.append("submission")
    if not picked and not needs:
        picked = ["strong"]
    return picked


def infer_focus_areas(classifications: dict) -> list[str]:
    areas = []
    if (classifications.get("total_tat") == "needs_work"
        or classifications.get("response_tat") == "needs_work"
        or classifications.get("onsite_tat") == "needs_work"):
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
# Talent grid (9-box) — Performance × Quality scoring
# ---------------------------------------------------------------------------
# The 9-box reads each solver as a point on two axes:
#
#   X = Performance     (speed + reliability of throughput)
#                       - Response TAT      (lower better)
#                       - Submission rate   (higher better)
#                       - Reject rate       (lower better, when data is available)
#   Y = Quality         (how well work lands + how trusted they are)
#                       - Client rating     (higher better)
#                       - Volume            (higher = more trusted with work)
#
# Each metric maps to a 0..100 score against its target; the axis score is the
# mean of the available metrics. Bands: 0-50 low, 50-75 medium, 75-100 high.
# Cells get human-readable labels (Star Performer / At Risk / etc).

_BOX_LABELS = {
    # (perf_band, quality_band) -> (label, cell index 1..9 reading top-left to bottom-right)
    (2, 2): ("Star Performer",        3),   # high perf, high quality
    (1, 2): ("Growing Talent",        2),   # med perf, high quality
    (0, 2): ("Untapped Potential",    1),   # low perf, high quality
    (2, 1): ("Rising Star",           6),   # high perf, med quality
    (1, 1): ("Solid Contributor",     5),   # med perf, med quality
    (0, 1): ("Inconsistent",          4),   # low perf, med quality
    (2, 0): ("Workhorse",             9),   # high perf, low quality
    (1, 0): ("Needs Coaching",        8),   # med perf, low quality
    (0, 0): ("At Risk",               7),   # low perf, low quality
}


def _score_lower_better(value: float | None, target: float) -> float | None:
    """Map a 'lower is better' metric to 0..100. Target gives 50, zero gives 100."""
    if value is None or pd.isna(value) or target <= 0:
        return None
    score = 100 * max(0.0, 1.0 - (value / (2 * target)))
    return min(100.0, score)


def _score_higher_better(value: float | None, target: float, cap_multiplier: float = 1.5) -> float | None:
    """Map a 'higher is better' metric to 0..100. Target gives ~67, cap_multiplier × target gives 100."""
    if value is None or pd.isna(value) or target <= 0:
        return None
    ceiling = target * cap_multiplier
    score = 100 * min(1.0, max(0.0, value / ceiling))
    return score


def _score_to_band(score: float | None) -> int | None:
    """Convert a 0..100 score to a band: 0 (low), 1 (medium), 2 (high)."""
    if score is None:
        return None
    if score >= 75:
        return 2
    if score >= 50:
        return 1
    return 0


def compute_talent_grid(stats: dict) -> dict | None:
    """Compute the 9-box talent-grid placement for a solver.

    Returns a dict with:
      - performance_score, quality_score: 0..100 floats
      - performance_band, quality_band:   0|1|2 (low|med|high)
      - cell:    1..9 cell index (1 = top-left = Untapped Potential, 9 = bottom-right = Workhorse)
      - label:   human-readable cell label
      - components: per-metric scores (for tooltips / debugging)
      - confidence: 'high' | 'medium' | 'low' based on how many metrics were available

    Returns None only if the solver has NO data at all (insufficient signal).
    """
    components = {}

    # --- Performance axis: TAT + submission rate + (optional) reject rate ---
    tat_score = _score_lower_better(
        stats.get("avg_total_tat_hrs"),
        TARGETS["total_tat_hours_max"],
    )
    sub_score = _score_higher_better(
        stats.get("submission_rate"),
        TARGETS["submission_rate_min"],
        cap_multiplier=1.0,   # 100% = full score
    )
    # Reject rate: lower better; assume target is 5% if not specified
    reject_target = TARGETS.get("reject_rate_max", 0.05)
    reject_score = _score_lower_better(stats.get("reject_rate"), reject_target)

    perf_components = {"total_tat": tat_score, "submission_rate": sub_score, "reject_rate": reject_score}
    perf_available = [s for s in perf_components.values() if s is not None]
    if not perf_available:
        return None
    performance_score = sum(perf_available) / len(perf_available)

    # --- Quality axis: client rating + volume ---
    rating_score = None
    n_ratings = stats.get("n_ratings", 0)
    rating = stats.get("avg_rating")
    if rating is not None and not pd.isna(rating) and n_ratings >= TARGETS["min_ratings_for_judgement"]:
        # Rating: 4.5 target -> 67, 5.0 -> 100
        rating_score = _score_higher_better(rating, TARGETS["rating_min"], cap_multiplier=5.0 / TARGETS["rating_min"])

    volume_score = _score_higher_better(
        stats.get("volume", 0),
        TARGETS["volume_min_monthly"],
        cap_multiplier=2.5,   # 2.5x target = full quality score (someone trusted with a lot of work)
    )

    quality_components = {"rating": rating_score, "volume": volume_score}
    quality_available = [s for s in quality_components.values() if s is not None]
    if not quality_available:
        # If we have no quality signal at all, fall back to using performance for both
        quality_score = performance_score
    else:
        quality_score = sum(quality_available) / len(quality_available)

    # --- Confidence ---
    total_available = len(perf_available) + len(quality_available)
    if total_available >= 4:
        confidence = "high"
    elif total_available >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    # --- Band + label ---
    perf_band = _score_to_band(performance_score)
    qual_band = _score_to_band(quality_score)
    label, cell = _BOX_LABELS[(perf_band, qual_band)]

    return {
        "performance_score": round(performance_score, 1),
        "quality_score": round(quality_score, 1),
        "performance_band": perf_band,
        "quality_band": qual_band,
        "cell": cell,
        "label": label,
        "confidence": confidence,
        "components": {**perf_components, **quality_components},
    }


# ---------------------------------------------------------------------------
# Name normalisation — for matching basket solvers to the registered solver list.
# ---------------------------------------------------------------------------

def normalise_name(s: Optional[str]) -> str:
    if not s or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).lower().strip()
    s = re.sub(r"['\u2019\"`]", "", s)
    s = re.sub(r"\b[a-z]\.\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _word_set_sim(a: str, b: str) -> float:
    aw, bw = set(a.split()), set(b.split())
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / max(len(aw), len(bw))


def _char_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    common = 0
    for c in set(a):
        common += min(a.count(c), b.count(c))
    return common / max(len(a), len(b))


def find_solver_match(name: str, registered: list[str]) -> Optional[str]:
    if not name or not registered:
        return None
    target = normalise_name(name)
    cands = [(r, normalise_name(r)) for r in registered]
    for orig, norm in cands:
        if norm == target:
            return orig
    for orig, norm in cands:
        if _word_set_sim(target, norm) >= 0.6:
            if len(set(target.split()) & set(norm.split())) >= 2:
                return orig
    for orig, norm in cands:
        if target and norm and target[0] == norm[0]:
            if abs(len(target) - len(norm)) <= 4 and _char_overlap(target, norm) >= 0.75:
                return orig
    return None


# ---------------------------------------------------------------------------
# Main: analyse a workbook
# ---------------------------------------------------------------------------

REQUIRED_SHEETS = ["TOTAL VALUED", "SOLVERS BASKET", "CLIENT RATING"]

# Tolerate common variants — names from Zoho exports show up with typos and
# casing differences. The right column name we look for once we've loaded
# the sheet is unchanged.
_SHEET_ALIASES = {
    "TOTAL VALUED": ["TOTAL VALUED", "TOTAL_VALUED", "TOTALVALUED"],
    "SOLVERS BASKET": ["SOLVERS BASKET", "SOLVER BASKET", "SOLVERS_BASKET"],
    "CLIENT RATING": ["CLIENT RATING", "CLIENT RAITNG", "CLIENT_RATING", "CLIENTRATING"],
}


def _resolve_sheet_name(required: str, available: list[str]) -> Optional[str]:
    """Find the actual sheet name in the workbook that matches `required`.

    Case-insensitive; tolerates whitespace and common typos via _SHEET_ALIASES.
    Returns the actual sheet name as it appears in the workbook, or None.
    """
    available_norm = {s.strip().upper(): s for s in available}
    aliases = _SHEET_ALIASES.get(required, [required])
    for alias in aliases:
        key = alias.strip().upper()
        if key in available_norm:
            return available_norm[key]
    return None


def analyse_workbook(input_path: Path) -> dict[str, Any]:
    """Read the workbook and return {team, solvers, targets} for DB insertion."""
    wb = pd.ExcelFile(input_path)

    sheet_map = {}
    for required in REQUIRED_SHEETS:
        actual = _resolve_sheet_name(required, wb.sheet_names)
        if actual is None:
            raise ValueError(
                f"Workbook missing required sheet '{required}'. "
                f"Available sheets: {', '.join(wb.sheet_names)}"
            )
        sheet_map[required] = actual

    tv = pd.read_excel(input_path, sheet_name=sheet_map["TOTAL VALUED"])
    sb = pd.read_excel(input_path, sheet_name=sheet_map["SOLVERS BASKET"])
    cr = pd.read_excel(input_path, sheet_name=sheet_map["CLIENT RATING"])

    for col in ("Valuation_Start", "Valuation_Date", "Schedule_date",
                "Requested_Date", "Approval_Status", "Solver"):
        if col not in tv.columns:
            raise ValueError(f"TOTAL VALUED missing column: {col}")
    for col in ("Request_Status", "Status", "Solver", "Initiated_Date"):
        if col not in sb.columns:
            raise ValueError(f"SOLVERS BASKET missing column: {col}")
    for col in ("Solver", "rating"):
        if col not in cr.columns:
            raise ValueError(f"CLIENT RATING missing column: {col}")

    # ------------------------------------------------------------------
    # TAT — computed from SOLVERS BASKET (jobs assigned to each solver
    # this period, regardless of whether they were ultimately approved).
    # PRIMARY metric: avg_total_tat_hrs = mean of (Valuation_Date − Requested_Date)
    # over basket rows where Request_Status == "Completed", capped at the
    # stuck-job threshold so multi-day outliers don't swamp the average.
    # ------------------------------------------------------------------
    sb = sb.copy()
    sb["_is_pending"] = sb["Request_Status"] == "Solver accept"
    sb["_is_completed"] = sb["Request_Status"] == "Completed"

    sb["total_tat_hrs"] = (
        sb["Valuation_Date"] - sb["Requested_Date"]
    ).dt.total_seconds() / 3600
    sb["response_tat_hrs"] = (
        sb["Valuation_Date"] - sb["Schedule_date"].fillna(sb["Requested_Date"])
    ).dt.total_seconds() / 3600
    sb["onsite_tat_hrs"] = (
        sb["Valuation_Date"] - sb["Valuation_Start"]
    ).dt.total_seconds() / 3600

    completed_basket = sb[sb["_is_completed"]].copy()
    completed_basket = completed_basket[
        (completed_basket["total_tat_hrs"] >= 0)
        & (completed_basket["onsite_tat_hrs"] >= 0)
        & (completed_basket["onsite_tat_hrs"] < 24)
    ]

    stuck_threshold = TARGETS["stuck_job_hours"]
    # Average TAT excludes "stuck" outliers (>72h) so a few extreme cases
    # don't dominate. Those still feed stuck_job_count below.
    tat_sample = completed_basket[completed_basket["total_tat_hrs"] <= stuck_threshold]

    per_solver_tat = tat_sample.groupby("Solver").agg(
        avg_total_tat_hrs=("total_tat_hrs", "mean"),
        median_total_tat_hrs=("total_tat_hrs", "median"),
        avg_response_tat_hrs=("response_tat_hrs", "mean"),
        median_response_tat_hrs=("response_tat_hrs", "median"),
        avg_onsite_tat_hrs=("onsite_tat_hrs", "mean"),
        median_onsite_tat_hrs=("onsite_tat_hrs", "median"),
        completed_count=("total_tat_hrs", "count"),
    )

    # Stuck jobs: completed basket rows where TAT > 72h
    stuck = completed_basket.assign(_stuck=completed_basket["total_tat_hrs"] > stuck_threshold) \
        .groupby("Solver")["_stuck"].agg(["sum", "count"])
    stuck.columns = ["stuck_job_count", "_t"]
    stuck["stuck_job_rate"] = stuck["stuck_job_count"] / stuck["_t"]
    stuck = stuck.drop(columns=["_t"])
    per_solver_tat = per_solver_tat.join(stuck, how="left")

    # ------------------------------------------------------------------
    # Volume = how many valuations the solver actually completed
    # (count of rows in TOTAL VALUED). Approval rate also from TOTAL VALUED.
    # ------------------------------------------------------------------
    tv = tv.copy()
    valued_volume = tv[tv["Solver"].notna()].groupby("Solver").size().rename("tv_volume")

    approval = tv[tv["Solver"].notna()].groupby("Solver").agg(
        total_attempts=("Approval_Status", "count"),
        approved_total=("Approval_Status", lambda s: (s == "Approved").sum()),
    )
    approval["approval_rate"] = approval["approved_total"] / approval["total_attempts"]
    per_solver_tv = approval[["total_attempts", "approval_rate"]].join(valued_volume, how="outer")

    # ------------------------------------------------------------------
    # SOLVERS BASKET aggregates — submission rate, assigned, pending
    # ------------------------------------------------------------------
    basket = sb[sb["Solver"].notna()].groupby("Solver").agg(
        assigned_count=("Vehicle_reg", "count"),
        valued_count=("_is_completed", "sum"),
        pending_count=("_is_pending", "sum"),
    )
    basket["valued_count"] = basket["valued_count"].astype(int)
    basket["pending_count"] = basket["pending_count"].astype(int)
    basket["submission_rate"] = basket.apply(
        lambda r: r["valued_count"] / r["assigned_count"] if r["assigned_count"] > 0 else float("nan"),
        axis=1,
    )

    # Jobs initiated by solver (from TOTAL VALUED)
    if "Initiator_Source" in tv.columns and "Initiated_by" in tv.columns:
        initiated = tv[
            (tv["Initiator_Source"] == "Solver")
            & tv["Initiated_by"].notna()
        ].groupby("Initiated_by").size().rename("jobs_initiated")
    else:
        initiated = pd.Series(dtype=int, name="jobs_initiated")

    # Ratings — drop zero ratings (empty/bogus)
    cr_valid = cr[(cr["rating"].notna()) & (cr["rating"] > 0)].copy()
    ratings_summary = cr_valid.groupby("Solver").agg(
        avg_rating=("rating", "mean"),
        n_ratings=("rating", "count"),
    )
    sub_cols = [c for c in ("presentation_rating", "professionalism_rating",
                            "punctuality_rating") if c in cr_valid.columns]
    if sub_cols:
        sub_means = cr_valid.groupby("Solver")[sub_cols].mean()
        ratings_summary = ratings_summary.join(sub_means)

    # Top pending statuses (from basket)
    pending_rows = sb[sb["_is_pending"]]
    if len(pending_rows) and "Status" in pending_rows.columns:
        pending_reasons = pending_rows.groupby(
            ["Solver", "Status"]
        ).size().unstack(fill_value=0)
    else:
        pending_reasons = pd.DataFrame()

    # Merge: TAT (from basket) + TV-derived (volume from TOTAL VALUED, approval rate)
    # + basket counts + ratings + self-initiated
    summary = per_solver_tat.join(per_solver_tv, how="outer")
    summary = summary.join(basket, how="outer")
    summary = summary.join(ratings_summary, how="outer")
    summary = summary.join(initiated, how="outer")

    # Volume = count from TOTAL VALUED (the actual valuations they completed).
    # If a solver has basket-completed but no TOTAL VALUED rows yet (e.g. timing
    # gap), fall back to basket valued_count so they don't show 0.
    summary["volume"] = summary["tv_volume"].fillna(summary.get("valued_count", 0)).fillna(0).astype(int)
    summary["total_attempts"] = summary["total_attempts"].fillna(0).astype(int)
    summary["valued_count"] = summary["valued_count"].fillna(0).astype(int)
    summary["assigned_count"] = summary["assigned_count"].fillna(0).astype(int)
    summary["pending_count"] = summary["pending_count"].fillna(0).astype(int)
    summary["n_ratings"] = summary["n_ratings"].fillna(0).astype(int)
    summary["stuck_job_count"] = summary["stuck_job_count"].fillna(0).astype(int)
    summary["jobs_initiated"] = summary["jobs_initiated"].fillna(0).astype(int)

    total_valued = int(summary["valued_count"].sum())       # basket completed
    total_volume = int(summary["volume"].sum())              # TOTAL VALUED rows
    total_pending = int(summary["pending_count"].sum())
    total_assigned = int(summary["assigned_count"].sum())
    total_initiated = int(summary["jobs_initiated"].sum())
    team_submission = (total_valued / total_assigned) if total_assigned else None

    team = {
        "avg_total_tat_hrs": float(tat_sample["total_tat_hrs"].mean()) if len(tat_sample) else None,
        "median_total_tat_hrs": float(tat_sample["total_tat_hrs"].median()) if len(tat_sample) else None,
        "avg_response_tat_hrs": float(tat_sample["response_tat_hrs"].mean()) if len(tat_sample) else None,
        "median_response_tat_hrs": float(tat_sample["response_tat_hrs"].median()) if len(tat_sample) else None,
        "avg_onsite_tat_hrs": float(tat_sample["onsite_tat_hrs"].mean()) if len(tat_sample) else None,
        "median_onsite_tat_hrs": float(tat_sample["onsite_tat_hrs"].median()) if len(tat_sample) else None,
        "avg_rating": float(cr_valid["rating"].mean()) if len(cr_valid) else None,
        "median_rating": float(cr_valid["rating"].median()) if len(cr_valid) else None,
        "avg_volume": float(summary["volume"].mean()) if len(summary) else 0.0,
        "median_volume": float(summary["volume"].median()) if len(summary) else 0.0,
        "team_submission_rate": team_submission,
        "median_submission_rate": float(summary["submission_rate"].dropna().median())
            if summary["submission_rate"].notna().any() else None,
        "total_solvers": int(len(summary)),
        "total_valuations": total_volume,                   # from TOTAL VALUED
        "total_jobs_assigned": total_assigned,
        "total_jobs_pending": total_pending,
        "total_jobs_initiated_by_solvers": total_initiated,
        "total_ratings_received": int(len(cr_valid)),
        "pct_stuck_jobs_team": float((completed_basket["total_tat_hrs"] > stuck_threshold).mean())
            if len(completed_basket) else 0.0,
    }

    def _f(v):
        if v is None or pd.isna(v): return None
        return float(v)

    solvers = []
    for name, row in summary.iterrows():
        if pd.isna(name) or not str(name).strip():
            continue

        stats = {
            "name": str(name),
            "volume": int(row["volume"]),
            "total_attempts": int(row["total_attempts"]),
            "valued_count": int(row["valued_count"]),
            "assigned_count": int(row["assigned_count"]),
            "jobs_initiated": int(row["jobs_initiated"]),
            "submission_rate": _f(row.get("submission_rate")),
            "avg_total_tat_hrs": _f(row.get("avg_total_tat_hrs")),
            "median_total_tat_hrs": _f(row.get("median_total_tat_hrs")),
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

        extra = {}
        for c in sub_cols:
            extra[c] = _f(row.get(c))

        if not pending_reasons.empty and name in pending_reasons.index:
            row_pr = pending_reasons.loc[name].sort_values(ascending=False)
            top = [(str(r), int(c)) for r, c in row_pr.items() if c > 0][:3]
            extra["top_pending_reasons"] = top
        else:
            extra["top_pending_reasons"] = []

        stats["extra"] = extra
        stats["classifications"] = classify_solver(stats)
        stats["training_modules"] = pick_training_modules(stats["classifications"])
        stats["focus_areas"] = infer_focus_areas(stats["classifications"])
        stats["talent_grid"] = compute_talent_grid(stats)
        solvers.append(stats)

    solvers.sort(key=lambda s: s["volume"], reverse=True)

    return {"team": team, "solvers": solvers, "targets": TARGETS}


# ---------------------------------------------------------------------------
# Period comparison
# ---------------------------------------------------------------------------

def compare_snapshots(current: dict, previous: dict) -> dict:
    """Per-metric deltas between two snapshots. Used for period-vs-period analysis
    and the 'how you compare to last month' coaching section."""
    if not current or not previous:
        return {}

    def _delta(c, p):
        if c is None or p is None or pd.isna(c) or pd.isna(p):
            return None
        return float(c - p)

    def _direction(d, lower_better):
        if d is None: return "na"
        if abs(d) < 1e-6: return "same"
        if lower_better:
            return "improved" if d < 0 else "worsened"
        return "improved" if d > 0 else "worsened"

    spec = [
        ("total_tat", True, "avg_total_tat_hrs"),
        ("response_tat", True, "avg_response_tat_hrs"),
        ("onsite_tat", True, "avg_onsite_tat_hrs"),
        ("submission_rate", False, "submission_rate"),
        ("volume", False, "volume"),
        ("rating", False, "avg_rating"),
        ("assigned", False, "assigned_count"),
        ("jobs_initiated", False, "jobs_initiated"),
    ]

    out = {"metrics": {}}
    for key, lower_better, field in spec:
        prev_v = previous.get(field)
        curr_v = current.get(field)
        d = _delta(curr_v, prev_v)
        out["metrics"][key] = {
            "previous": prev_v,
            "current": curr_v,
            "delta": d,
            "direction": _direction(d, lower_better),
            "lower_is_better": lower_better,
        }

    improvements = [k for k, v in out["metrics"].items() if v["direction"] == "improved"]
    regressions = [k for k, v in out["metrics"].items() if v["direction"] == "worsened"]

    # Map internal keys to human-readable labels for the headline
    _LABELS = {
        "total_tat": "average time per job",
        "response_tat": "response TAT",
        "onsite_tat": "on-site TAT",
        "submission_rate": "submission rate",
        "volume": "volume",
        "rating": "client rating",
        "assigned": "jobs assigned",
        "jobs_initiated": "self-initiated work",
    }
    def _human(keys):
        return ", ".join(_LABELS.get(k, k) for k in keys)

    if improvements and not regressions:
        out["headline"] = "improved across the board"
    elif regressions and not improvements:
        out["headline"] = "regressed on several metrics"
    elif improvements and regressions:
        out["headline"] = f"improved on {_human(improvements[:2])}; watch {_human(regressions[:2])}"
    else:
        out["headline"] = "broadly steady"
    out["improvements"] = improvements
    out["regressions"] = regressions

    return out


# ---------------------------------------------------------------------------
# Regional aggregation
# ---------------------------------------------------------------------------

def aggregate_by_region(solver_snapshots: list[dict], solver_region_map: dict[str, dict]) -> list[dict]:
    """Group solver snapshots by region and compute regional metrics."""
    from collections import defaultdict
    by_region = defaultdict(list)

    for snap in solver_snapshots:
        info = solver_region_map.get(snap["name"])
        region = info["region"] if info else "Unassigned"
        by_region[region].append(snap)

    regions = []
    for region, snaps in by_region.items():
        total_solvers = len(snaps)
        total_assigned = sum(s["assigned_count"] for s in snaps)
        total_valued = sum(s["valued_count"] for s in snaps)
        total_pending = sum(s["pending_count"] for s in snaps)
        total_initiated = sum(s["jobs_initiated"] for s in snaps)

        ttats = [s["avg_total_tat_hrs"] for s in snaps if s.get("avg_total_tat_hrs") is not None]
        rtats = [s["avg_response_tat_hrs"] for s in snaps if s["avg_response_tat_hrs"] is not None]
        otats = [s["avg_onsite_tat_hrs"] for s in snaps if s["avg_onsite_tat_hrs"] is not None]
        ratings = [s["avg_rating"] for s in snaps if s["avg_rating"] is not None
                   and s["n_ratings"] >= TARGETS["min_ratings_for_judgement"]]

        avg_total_tat = sum(ttats) / len(ttats) if ttats else None
        avg_rtat = sum(rtats) / len(rtats) if rtats else None
        avg_otat = sum(otats) / len(otats) if otats else None
        avg_rating = sum(ratings) / len(ratings) if ratings else None
        submission_rate = (total_valued / total_assigned) if total_assigned else None

        jobs_per_solver = total_assigned / total_solvers if total_solvers else 0
        if jobs_per_solver >= TARGETS["regional_overload_jobs_per_solver"]:
            staffing = "overloaded"
        elif jobs_per_solver < TARGETS["regional_under_jobs_per_solver"]:
            staffing = "under_utilised"
        else:
            staffing = "balanced"

        # Performance judged on TOTAL TAT (the primary metric) + submission rate
        if submission_rate is None or avg_total_tat is None:
            performance = "insufficient_data"
        elif submission_rate >= TARGETS["submission_rate_min"] and avg_total_tat <= TARGETS["total_tat_hours_max"]:
            performance = "strong"
        elif submission_rate < 0.70 or avg_total_tat > TARGETS["total_tat_hours_max"] * 1.5:
            performance = "needs_improvement"
        else:
            performance = "on_track"

        needs_coaching = sum(1 for s in snaps if s.get("focus_areas") and "strong" not in s.get("focus_areas", []))
        strong_count = sum(1 for s in snaps if "strong" in s.get("focus_areas", []))

        locations = sorted({
            (solver_region_map.get(s["name"]) or {}).get("location", "")
            for s in snaps
            if (solver_region_map.get(s["name"]) or {}).get("location")
        })

        regions.append({
            "region": region,
            "solver_count": total_solvers,
            "total_assigned": total_assigned,
            "total_valued": total_valued,
            "total_pending": total_pending,
            "total_jobs_initiated": total_initiated,
            "jobs_per_solver": round(jobs_per_solver, 1),
            "submission_rate": submission_rate,
            "avg_total_tat_hrs": avg_total_tat,
            "avg_response_tat_hrs": avg_rtat,
            "avg_onsite_tat_hrs": avg_otat,
            "avg_rating": avg_rating,
            "staffing": staffing,
            "performance": performance,
            "needs_coaching_count": needs_coaching,
            "strong_count": strong_count,
            "locations": locations,
            "solver_names": sorted(s["name"] for s in snaps),
        })

    regions.sort(key=lambda r: (r["region"] == "Unassigned", -r["total_assigned"]))
    return regions
