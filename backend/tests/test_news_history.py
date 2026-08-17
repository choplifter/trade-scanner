from datetime import date, datetime, timedelta, timezone

from app.scanners.news_history import catalyst_days
from app.services.market_clock import ET

# Real NYSE sessions: Thu 2026-08-13, Fri 2026-08-14 (Sat/Sun between the
# latter and Mon 2026-08-17).
THU, FRI, MON = date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17)


def _item(title, when: datetime, publisher="GlobeNewsWire"):
    return {
        "title": title,
        "publisher": publisher,
        "publishedDate": when.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _et(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm)


def test_after_close_release_is_attributed_to_the_next_session():
    """The classic overnight gap driver. Attributing it to the session that
    had already ended would be backwards."""
    items = [_item("Acme Reports Q2 Results", _et(THU, 16, 5))]
    found = catalyst_days(items, [THU, FRI])
    assert FRI in found
    assert THU not in found


def test_intraday_release_belongs_to_its_own_session():
    items = [_item("Acme Wins $80M Contract", _et(FRI, 10, 30))]
    assert catalyst_days(items, [THU, FRI]) == {FRI: "Acme Wins $80M Contract"}


def test_friday_evening_news_carries_to_monday_across_the_weekend():
    # Monday's open minus 18h is Sunday 15:30, so a Friday-evening release is
    # outside Monday's window -- it belonged to Friday, and Friday traded.
    items = [_item("Acme Announces Merger", _et(FRI, 18, 0))]
    found = catalyst_days(items, [FRI, MON])
    assert MON not in found


def test_the_latest_qualifying_story_in_the_window_wins():
    items = [
        _item("Acme Announces Offering", _et(FRI, 9, 0)),
        _item("Acme Wins Contract", _et(FRI, 14, 0)),
    ]
    assert catalyst_days(items, [FRI])[FRI] == "Acme Wins Contract"


def test_noise_never_counts_as_a_catalyst():
    """Litigation notices are published after a collapse, so counting them
    would manufacture a correlation between 'had news' and 'already fell'."""
    items = [
        _item("Acme Investor Alert: Law Offices of Someone", _et(FRI, 10, 0), "PRNewsWire"),
        _item("Is Acme a Buy?", _et(FRI, 11, 0), "Seeking Alpha"),
    ]
    assert catalyst_days(items, [FRI]) == {}


def test_non_trading_days_are_never_flagged():
    saturday = date(2026, 8, 15)
    items = [_item("Acme Announces Merger", _et(saturday, 12, 0))]
    assert saturday not in catalyst_days(items, [saturday])


def test_undated_or_unparseable_items_are_dropped():
    assert catalyst_days([{"title": "Acme Wins Contract", "publisher": "GlobeNewsWire"}], [FRI]) == {}
