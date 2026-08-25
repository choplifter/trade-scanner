"""The symbol panel's news window: never narrower than the scanner's."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.market_data.fmp_news import recent_news_cutoff
from app.news.client import symbol_news_cutoff

ET = ZoneInfo("America/New_York")


def test_a_weekday_afternoon_reaches_the_full_48h_back():
    """The BCTX case: Tuesday 12:23 ET. The session anchor starts at 15:30
    ET Monday; the scanner's 48h starts Sunday. Monday's 07:33 ET press
    release drove the scanner badge and must reach the panel too."""
    now = datetime(2026, 8, 25, 12, 23, tzinfo=ET).astimezone(UTC)
    story = datetime(2026, 8, 24, 7, 33, tzinfo=ET).astimezone(UTC)

    cutoff = symbol_news_cutoff(now)

    assert cutoff == now - timedelta(hours=48)
    assert cutoff < story  # in the window
    assert recent_news_cutoff(now) > story  # ...which the anchor alone excluded


def test_a_monday_premarket_still_reaches_fridays_session():
    """The case the session anchor exists for: a flat 48h from Monday 08:00
    ET stops at Saturday, while the last session that has begun is Friday's.
    The wider window is the anchor's, and it stays."""
    now = datetime(2026, 8, 24, 8, 0, tzinfo=ET).astimezone(UTC)
    friday_story = datetime(2026, 8, 21, 11, 0, tzinfo=ET).astimezone(UTC)

    cutoff = symbol_news_cutoff(now)

    assert cutoff == recent_news_cutoff(now)
    assert cutoff < now - timedelta(hours=48)
    assert cutoff < friday_story


def test_never_narrower_than_either_window():
    for day in range(24, 31):  # a full week of ET noons
        now = datetime(2026, 8, day, 12, 0, tzinfo=ET).astimezone(UTC)
        cutoff = symbol_news_cutoff(now)
        assert cutoff <= recent_news_cutoff(now)
        assert cutoff <= now - timedelta(hours=48)
