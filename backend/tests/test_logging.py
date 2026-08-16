import logging

from app.core.logging import RedactSecretsFilter


def _render(msg, *args) -> str:
    """Run a record through the filter and return what would be logged."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1, msg=msg, args=args,
        exc_info=None,
    )
    assert RedactSecretsFilter().filter(record) is True, "records must still be logged"
    return record.getMessage()


class _FakeURL:
    """httpx logs request.url, which is an httpx.URL object rather than a str."""

    def __init__(self, url: str) -> None:
        self._url = url

    def __str__(self) -> str:
        return self._url


def test_redacts_apikey_from_an_httpx_style_record():
    # The exact shape httpx logs at INFO: URL passed as a %s arg, not in the
    # message template.
    rendered = _render(
        'HTTP Request: %s %s "%s %d %s"',
        "GET",
        _FakeURL("https://financialmodelingprep.com/stable/quote?symbol=%5EVIX&apikey=s3cr3t"),
        "HTTP/1.1",
        200,
        "OK",
    )
    assert "s3cr3t" not in rendered
    assert "apikey=REDACTED" in rendered
    # Everything else about the line survives.
    assert "symbol=%5EVIX" in rendered
    assert 'HTTP Request: GET' in rendered
    assert '"HTTP/1.1 200 OK"' in rendered


def test_redacts_a_secret_embedded_in_the_message_itself():
    rendered = _render("fetching https://example.com/x?apikey=abc123 now")
    assert "abc123" not in rendered
    assert "apikey=REDACTED" in rendered


def test_stops_at_the_next_query_param():
    rendered = _render("https://example.com/x?apikey=abc123&symbol=AAPL")
    assert rendered == "https://example.com/x?apikey=REDACTED&symbol=AAPL"


def test_is_case_insensitive_about_the_param_name():
    for name in ("apikey", "apiKey", "API_KEY", "token", "access_token", "password", "secret"):
        rendered = _render(f"https://example.com/x?{name}=leaked")
        assert "leaked" not in rendered, name


def test_leaves_ordinary_params_and_args_alone():
    rendered = _render("universe: %s symbols at %s", 249, "https://example.com/x?symbol=AAPL")
    assert rendered == "universe: 249 symbols at https://example.com/x?symbol=AAPL"


def test_does_not_match_a_param_that_merely_ends_in_key():
    # `monkey=` shouldn't be treated as a secret just because it ends in "key".
    rendered = _render("https://example.com/x?monkey=business")
    assert rendered == "https://example.com/x?monkey=business"


def test_redacts_secrets_in_dict_style_args():
    # `logger.info("%(url)s", {"url": ...})` passes the mapping as a 1-tuple,
    # which LogRecord unwraps into record.args -- so the filter sees a dict.
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="calling %(url)s", args=({"url": "https://example.com/x?apikey=abc123"},),
        exc_info=None,
    )
    assert isinstance(record.args, dict), "LogRecord should have unwrapped the mapping"
    RedactSecretsFilter().filter(record)
    assert "abc123" not in record.getMessage()
    assert "apikey=REDACTED" in record.getMessage()


def test_a_record_with_no_secret_is_untouched():
    rendered = _render("FINRA short-interest file loaded: %s (%d symbols)", "20260715", 22375)
    assert rendered == "FINRA short-interest file loaded: 20260715 (22375 symbols)"
