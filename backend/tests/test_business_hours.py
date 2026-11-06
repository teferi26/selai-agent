"""is_within_business_hours — pure helper behind ai_mode=out_of_hours (52)."""
from __future__ import annotations

from datetime import datetime

from app.services.conversation_service import (
    DEFAULT_BUSINESS_HOURS,
    is_within_business_hours,
)

# 2026-07-22 is a Wednesday; 2026-07-25 is a Saturday.
WEDNESDAY_NOON = datetime(2026, 7, 22, 12, 0)
WEDNESDAY_NIGHT = datetime(2026, 7, 22, 22, 30)
SATURDAY_NOON = datetime(2026, 7, 25, 12, 0)


def test_default_hours_weekday_noon_is_inside():
    assert is_within_business_hours(WEDNESDAY_NOON) is True
    assert is_within_business_hours(WEDNESDAY_NOON, None) is True


def test_default_hours_weekday_night_is_outside():
    assert is_within_business_hours(WEDNESDAY_NIGHT) is False


def test_default_hours_weekend_is_outside():
    assert is_within_business_hours(SATURDAY_NOON) is False


def test_default_hours_boundaries():
    # Start is inclusive, end is exclusive.
    assert is_within_business_hours(datetime(2026, 7, 22, 9, 0)) is True
    assert is_within_business_hours(datetime(2026, 7, 22, 8, 59)) is False
    assert is_within_business_hours(datetime(2026, 7, 22, 19, 0)) is False
    assert is_within_business_hours(datetime(2026, 7, 22, 18, 59)) is True


def test_custom_hours_and_days():
    bh = {"start": "16:00", "end": "20:00", "days": [5, 6]}  # weekends only
    assert is_within_business_hours(datetime(2026, 7, 25, 17, 0), bh) is True
    assert is_within_business_hours(datetime(2026, 7, 25, 12, 0), bh) is False
    assert is_within_business_hours(datetime(2026, 7, 22, 17, 0), bh) is False


def test_malformed_config_falls_back_to_default():
    assert is_within_business_hours(WEDNESDAY_NOON, {"start": "not-a-time"}) is True
    assert is_within_business_hours(WEDNESDAY_NIGHT, {"days": "todos"}) is False
    assert is_within_business_hours(SATURDAY_NOON, {}) is False


def test_default_constant_is_mon_to_fri_9_19():
    assert DEFAULT_BUSINESS_HOURS == {"start": "09:00", "end": "19:00", "days": [0, 1, 2, 3, 4]}
