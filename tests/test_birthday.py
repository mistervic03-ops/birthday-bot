from datetime import date

from birthday_dates import birthday_targets_for, get_effective_birthday, is_leap_year


def test_is_leap_year() -> None:
    assert is_leap_year(2024) is True
    assert is_leap_year(2100) is False
    assert is_leap_year(2000) is True


def test_effective_birthday_maps_feb_29_to_feb_28_in_non_leap_year() -> None:
    assert get_effective_birthday(2, 29, today=date(2025, 1, 1)) == (2, 28)


def test_effective_birthday_keeps_feb_29_in_leap_year() -> None:
    assert get_effective_birthday(2, 29, today=date(2024, 1, 1)) == (2, 29)


def test_birthday_targets_include_feb_29_on_non_leap_feb_28() -> None:
    assert birthday_targets_for(date(2025, 2, 28)) == [(2, 28), (2, 29)]


def test_birthday_targets_do_not_include_feb_29_on_leap_feb_28() -> None:
    assert birthday_targets_for(date(2024, 2, 28)) == [(2, 28)]
