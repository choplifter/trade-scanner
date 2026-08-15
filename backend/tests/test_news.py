from app.market_data.news import is_roundup_headline


def test_is_roundup_headline_numbered_template():
    assert is_roundup_headline("12 Health Care Stocks Moving In Wednesday's Intraday Session") is True


def test_is_roundup_headline_numbered_template_with_direction_suffix():
    # The (Gainer)/(Loser) suffix _append_mover_directions appends is still
    # present on the cached headline by the time this gets checked again.
    assert is_roundup_headline("12 Industrials Stocks Moving In Thursday's After-Market Session (Gainer)") is True


def test_is_roundup_headline_numbered_template_other_topics():
    # Benzinga reuses the same numbered-listicle template for non-"Moving"
    # topics -- verified live in production data (2026-08-15).
    assert is_roundup_headline("4 Health Care Stocks With Whale Alerts In Today's Session") is True
    assert is_roundup_headline("10 Information Technology Stocks Whale Activity In Today's Session") is True


def test_is_roundup_headline_and_other_stocks_template():
    assert is_roundup_headline("Accelerant Holdings, CorMedix, Birkenstock And Other Big Stocks Moving Higher On Thursday") is True


def test_is_roundup_headline_and_other_stocks_template_lower_direction():
    assert is_roundup_headline("Cerebras Systems, Securitize And Other Big Stocks Moving Lower In Thursday's Pre-Market Session") is True


def test_is_roundup_headline_and_other_stocks_template_case_insensitive():
    assert is_roundup_headline("Foo Corp And Other Stocks Moving higher On Friday") is True


def test_is_roundup_headline_false_for_plain_company_news():
    assert is_roundup_headline("Omeros Corporation Announces FDA Approval For New Drug") is False


def test_is_roundup_headline_false_for_speculative_single_stock_post():
    assert is_roundup_headline(
        'Charles Gasparino Posts On X "SCOOP: There is optimism inside the Ellison camp..."'
    ) is False


def test_is_roundup_headline_false_for_compound_why_headline():
    # Real, symbol-specific lead clause even though it trails into a
    # roundup teaser -- deliberately NOT classified as a roundup mention
    # since the "Why <Symbol> Shares Are Trading Higher" part is genuine
    # per-symbol content, unlike the pure listicle templates above.
    assert is_roundup_headline(
        "Why Omeros Shares Are Trading Higher By Over 17%; Here Are 20 Stocks Moving Premarket"
    ) is False
