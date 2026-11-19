"""Tiny key-value store for admin-editable policy settings (fleet bonus
rate/minimum, TAT travel allowance, dormancy threshold), backed by the
AppSetting table. Kept separate from routes.py/extensions.py so both can
import it without a circular import.
"""
from __future__ import annotations
from sqlalchemy.orm import Session

from app import models, analysis

SETTING_DEFAULTS = {
    "fleet_bonus": {"rate": analysis.TARGETS["fleet_bonus_rate"], "min_size": analysis.TARGETS["fleet_min_size"]},
    "tat_allowance": {
        "major_regions": analysis.TARGETS["tat_allowance_major_regions"],
        "hours": analysis.TARGETS["tat_allowance_hours"],
    },
    "dormancy": {"periods": analysis.TARGETS["dormancy_periods"]},
}


def get_setting(db: Session, key: str) -> dict:
    row = db.get(models.AppSetting, key)
    if row:
        return row.value
    return dict(SETTING_DEFAULTS.get(key, {}))


def set_setting(db: Session, key: str, value: dict) -> dict:
    row = db.get(models.AppSetting, key)
    if row:
        row.value = value
    else:
        row = models.AppSetting(key=key, value=value)
        db.add(row)
    db.commit()
    return value
