"""Shared monthly/yearly occurrence dates for generation, audit and projections."""

from calendar import monthrange
from collections.abc import Iterator, Mapping
from datetime import date
from typing import Any


def occurrence_dates(rule: Mapping[str, Any], start: date, end: date) -> Iterator[date]:
    anchor = max(start, rule["start_date"])
    limit = min(end, rule["end_date"]) if rule.get("end_date") else end
    index = anchor.year * 12 + anchor.month - 1
    last = limit.year * 12 + limit.month - 1
    while index <= last:
        year, month = divmod(index, 12)
        month += 1
        if rule["frequency"] == "MONTHLY" or (
            rule["frequency"] == "YEARLY" and month == rule["month_of_year"]
        ):
            due = date(year, month, min(rule["due_day"], monthrange(year, month)[1]))
            if anchor <= due <= limit:
                yield due
        index += 1


def next_occurrence(rule: Mapping[str, Any], anchor: date) -> date | None:
    if not rule.get("active", True):
        return None
    return next(occurrence_dates(rule, anchor, date.max), None)
