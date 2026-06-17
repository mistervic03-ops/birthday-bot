from __future__ import annotations

from datetime import date


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def get_effective_birthday(
    month: int, day: int, *, today: date | None = None
) -> tuple[int, int]:
    today = today or date.today()
    if month == 2 and day == 29 and not is_leap_year(today.year):
        return (2, 28)
    return (month, day)


def birthday_targets_for(today: date) -> list[tuple[int, int]]:
    targets = {(today.month, today.day)}
    if today.month == 2 and today.day == 28 and not is_leap_year(today.year):
        targets.add((2, 29))
    return sorted(targets)

