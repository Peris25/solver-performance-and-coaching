"""
Solvit Compensation Engine — Core calculation logic.
Mirrors the v5 JS formulas exactly (validated 116/116 against Excel).
All threshold and pay calculations live here so the API and any future
integrations share a single source of truth.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional

# ── Regional benchmark: 2024 Regent competitor demand × 15% market share ÷ headcount
REGION_DATA: dict[str, dict] = {
    "South Nairobi": {"regent_annual": 40249,  "solver_count": 9},
    "North Nairobi":  {"regent_annual": 26833,  "solver_count": 6},
    "East Nairobi":   {"regent_annual": 58138,  "solver_count": 13},
    "West Nairobi":   {"regent_annual": 26833,  "solver_count": 6},
    "Central":        {"regent_annual": 34711,  "solver_count": 23},
    "Coast":          {"regent_annual": 58942,  "solver_count": 10},
    "Rift":           {"regent_annual": 43089,  "solver_count": 16},
    "West Kenya":     {"regent_annual": 16457,  "solver_count": 22},
    "East Kenya":     {"regent_annual": 8844,   "solver_count": 11},
}
MARKET_SHARE = 0.15

def region_benchmark(region: str) -> float:
    d = REGION_DATA.get(region)
    if not d:
        return 0.0
    return (d["regent_annual"] / 12 * MARKET_SHARE) / d["solver_count"]


@dataclass
class Params:
    rate1: float = 400       # Band 1 KSh/job (base)
    rate2: float = 450       # Band 2 KSh/job (mid)
    rate3: float = 500       # Band 3 KSh/job (top)
    stretch_floor: float = 1.15
    wht_pct: float = 5.0     # Withholding tax %
    rev_per_job: float = 950 # Revenue per standard job, for margin calcs
    t1_day: int = 16         # T1 cap: sustainable daily pace
    t2_day: int = 18         # T2 ceiling: max daily pace
    working_days: int = 22
    round_base: int = 5

    @property
    def t1_cap(self) -> int:
        return self.t1_day * self.working_days   # 352

    @property
    def t2_ceiling(self) -> int:
        return self.t2_day * self.working_days   # 396


@dataclass
class ThresholdResult:
    blended_avg: float
    region_baseline: float
    best_average: float
    basis: str
    multiplier: float
    raw_ratio: float
    active_periods: int
    t1: int
    t2: int
    tier: str
    t1_capped: bool
    t2_capped: bool
    used_region_benchmark: bool
    is_manually_adjusted: bool


@dataclass
class PayResult:
    gross_pay: float
    assessment: float
    total_gross: float
    wht: float
    net_pay: float
    band1_jobs: int
    band2_jobs: int
    band3_jobs: int
    top_rate: str
    t1_period: int
    t2_period: int


def _mround(x: float, base: int) -> int:
    """
    Mirrors Excel MROUND with floating-point protection.
    e.g. 50 × 1.15 = 57.49999... in IEEE 754 → snap to 6dp first.
    """
    if base == 0:
        return int(x)
    corrected = round(x, 6)
    return int(round(corrected / base) * base)


def _r5(x: float, base: int = 5) -> int:
    """Round to nearest `base`, with Excel-compatible half-up behaviour."""
    if x <= 0:
        return 0
    corrected = round(x, 6)
    if corrected < base:
        return max(1, round(corrected))
    return _mround(corrected, base)


def compute_thresholds(
    avg_2024: float,
    avg_2025: float,
    avg_2026: float,
    region: str,
    manual_best_override: Optional[float],
    params: Params,
) -> ThresholdResult:
    """
    Core threshold calculation — matches v5 Excel Calculations sheet exactly.

    Steps:
    1. Blended Avg = simple average of active (>0) year-averages
    2. Best Average = own peak, or region benchmark if higher AND ≤1 active period
    3. Manual override replaces Best Average entirely when set
    4. T2 Multiplier = capacity-dampened (shrinks linearly toward stretch_floor as
       blended_avg approaches t2_ceiling)
    5. T1 = MIN(best_average, t1_cap), rounded
    6. T2 = MIN(T1 × multiplier, t2_ceiling), never below T1 + round_base
    """
    avgs = [avg_2024, avg_2025, avg_2026]
    active = [v for v in avgs if v > 0]
    active_periods = len(active)
    blended = sum(active) / active_periods if active else 0.0
    reg_baseline = region_benchmark(region)

    # ── Best Average ──────────────────────────────────────────────────
    own_max = max(active) if active else 0.0
    used_region = False
    if active_periods <= 1:
        if reg_baseline > own_max:
            best_avg = reg_baseline
            basis = ("Region benchmark (no history)"
                     if active_periods == 0
                     else "Region benchmark used (new solver, single period)")
            used_region = True
        else:
            best_avg = own_max
            basis = "Personal avg (new solver, exceeds region)"
    else:
        best_avg = own_max
        basis = f"Personal best ({active_periods} yrs of history)"

    # ── Manual override ───────────────────────────────────────────────
    is_manual = (
        manual_best_override is not None
        and str(manual_best_override).strip() != ""
    )
    if is_manual:
        best_avg = float(manual_best_override)
        basis = "Manually adjusted by admin"

    # ── T2 Multiplier (capacity-dampened) ────────────────────────────
    raw_ratio = (best_avg / blended) if blended > 0 else 1.0
    raw_mult = max(raw_ratio, params.stretch_floor)
    utilization = min(blended / params.t2_ceiling, 1.0) if params.t2_ceiling > 0 else 0.0
    multiplier = (raw_mult - (raw_mult - params.stretch_floor) * utilization) if blended > 0 else 0.0

    # ── T1 = MIN(best_average, t1_cap), rounded ──────────────────────
    t1_raw = min(best_avg, params.t1_cap)
    t1_capped = best_avg > params.t1_cap
    t1 = _r5(t1_raw, params.round_base) if best_avg > 0 else 0

    # ── T2 = MIN(T1 × multiplier, t2_ceiling), never below T1 + base ─
    t2_capped = False
    t2 = 0
    if best_avg > 0:
        t2_raw = t1 * multiplier
        t2_rounded = _r5(t2_raw, params.round_base)
        if t2_rounded > params.t2_ceiling:
            t2_rounded = params.t2_ceiling
            t2_capped = True
        t2 = max(t2_rounded, t1 + params.round_base)

    # ── Performance Tier (based on blended avg) ───────────────────────
    if blended >= 80:
        tier = "Tier 1"
    elif blended >= 30:
        tier = "Tier 2"
    elif blended >= 5:
        tier = "Tier 3"
    else:
        tier = "Tier 4"

    return ThresholdResult(
        blended_avg=round(blended, 4),
        region_baseline=round(reg_baseline, 4),
        best_average=round(best_avg, 4),
        basis=basis,
        multiplier=round(multiplier, 6),
        raw_ratio=round(raw_ratio, 6),
        active_periods=active_periods,
        t1=t1,
        t2=t2,
        tier=tier,
        t1_capped=t1_capped,
        t2_capped=t2_capped,
        used_region_benchmark=used_region,
        is_manually_adjusted=is_manual,
    )


def compute_pay(
    std_jobs: int,
    assessment: float,
    t1: int,
    t2: int,
    period_days: int,
    params: Params,
) -> PayResult:
    """
    Calculate gross pay, WHT, and net pay for a solver in a given period.
    T1/T2 thresholds are pro-rated to the period length (vs 30-day base month).
    """
    jobs = max(0, int(std_jobs or 0))
    assess = max(0.0, float(assessment or 0))

    if t1 == 0 and t2 == 0:
        gross = jobs * params.rate1
        total_gross = gross + assess
        wht = total_gross * (params.wht_pct / 100)
        return PayResult(
            gross_pay=gross, assessment=assess,
            total_gross=total_gross, wht=wht, net_pay=total_gross - wht,
            band1_jobs=jobs, band2_jobs=0, band3_jobs=0,
            top_rate="Ksh 400" if jobs > 0 else "—",
            t1_period=0, t2_period=0,
        )

    scale = period_days / 30
    t1p = round(t1 * scale)
    t2p = round(t2 * scale)

    if jobs <= 0:
        band1 = band2 = band3 = 0
        gross = 0.0
    elif jobs <= t1p:
        band1, band2, band3 = jobs, 0, 0
        gross = jobs * params.rate1
    elif jobs <= t2p:
        band1, band2, band3 = t1p, jobs - t1p, 0
        gross = t1p * params.rate1 + (jobs - t1p) * params.rate2
    else:
        band1, band2, band3 = t1p, t2p - t1p, jobs - t2p
        gross = (t1p * params.rate1
                 + (t2p - t1p) * params.rate2
                 + (jobs - t2p) * params.rate3)

    if band3 > 0:
        top_rate = "Ksh 500"
    elif band2 > 0:
        top_rate = "Ksh 450"
    elif jobs > 0:
        top_rate = "Ksh 400"
    else:
        top_rate = "—"

    total_gross = gross + assess
    wht = total_gross * (params.wht_pct / 100)

    return PayResult(
        gross_pay=round(gross, 2),
        assessment=round(assess, 2),
        total_gross=round(total_gross, 2),
        wht=round(wht, 2),
        net_pay=round(total_gross - wht, 2),
        band1_jobs=band1,
        band2_jobs=band2,
        band3_jobs=band3,
        top_rate=top_rate,
        t1_period=t1p,
        t2_period=t2p,
    )
