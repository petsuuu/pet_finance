from datetime import date
from typing import Any

from app.services.occurrences import next_occurrence, occurrence_dates


def test_annual_leap_day_and_end_boundaries() -> None:
    rule: dict[str, Any] = {
        "frequency": "YEARLY",
        "month_of_year": 2,
        "due_day": 29,
        "start_date": date(2026, 3, 1),
        "end_date": date(2028, 12, 31),
    }
    assert list(occurrence_dates(rule, date(2026, 1, 1), date(2029, 12, 31))) == [
        date(2027, 2, 28),
        date(2028, 2, 29),
    ]
    assert next_occurrence(rule, date(2028, 3, 1)) is None
    rule["active"] = False
    assert next_occurrence(rule, date(2027, 1, 1)) is None


def test_monthly_dates_remain_clamped() -> None:
    rule: dict[str, Any] = {
        "frequency": "MONTHLY",
        "due_day": 31,
        "start_date": date(2028, 1, 1),
        "end_date": date(2028, 3, 30),
    }
    assert list(occurrence_dates(rule, date(2028, 1, 31), date(2028, 4, 1))) == [
        date(2028, 1, 31),
        date(2028, 2, 29),
    ]
