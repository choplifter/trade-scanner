from datetime import date

from app.services.market_clock import day_of_week, is_opex_week


def test_day_of_week_labels():
    assert day_of_week(date(2026, 8, 8)) == "Saturday"
    assert day_of_week(date(2026, 8, 10)) == "Monday"


def test_is_opex_week_third_friday_of_month():
    # August 2026: Fridays fall on the 7th, 14th, 21st, 28th -- the 21st is
    # the third Friday, so its Mon-Fri week (17th-21st) is the OpEx week.
    assert is_opex_week(date(2026, 8, 21)) is True
    assert is_opex_week(date(2026, 8, 17)) is True  # Monday of that week
    assert is_opex_week(date(2026, 8, 14)) is False  # second Friday
    assert is_opex_week(date(2026, 8, 24)) is False  # week after


def test_is_opex_week_handles_month_boundary():
    # January 2027: 1st is a Friday -- the third Friday is the 15th.
    assert is_opex_week(date(2027, 1, 15)) is True
    assert is_opex_week(date(2027, 1, 1)) is False
