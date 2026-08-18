"""Secrets must not reach a log record in any form, tracebacks included.

The traceback case is the one that actually bit: httpx builds its
HTTPStatusError message out of the full request URL, so an FMP call that
raised for status wrote the API key in clear text into the stack trace while
the INFO line for the same request, two lines above, read "apikey=REDACTED".
A logging.Filter cannot catch it -- the traceback is rendered from exc_info
by the formatter, after every filter has run.
"""

import logging

from app.core.logging import RedactingFormatter, RedactSecretsFilter

# A synthetic value, deliberately not a real key. The point of this file
# is that secrets never reach a log; putting a live credential in it to
# prove that would defeat the exercise, and secret scanners are right to
# flag it. Shape matters, not provenance: 32 alphanumerics, matching what
# FMP issues, so the redaction regex is exercised the same way.
SECRET = "FAKEKEYFAKEKEYFAKEKEYFAKEKEY1234"
URL = f"https://financialmodelingprep.com/stable/profile?symbol=IPST&apikey={SECRET}"


def _render(record: logging.LogRecord) -> str:
    RedactSecretsFilter().filter(record)
    return RedactingFormatter(logging.Formatter("%(message)s")).format(record)


def _record(msg, args=(), exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("t", logging.ERROR, __file__, 1, msg, args, exc_info)


def test_secret_stripped_from_the_message():
    out = _render(_record("GET %s", (URL,)))
    assert SECRET not in out
    assert "apikey=REDACTED" in out


def test_secret_stripped_from_a_traceback():
    """The regression this file exists for."""
    try:
        raise RuntimeError(f"Client error '429 Too Many Requests' for url '{URL}'")
    except RuntimeError:
        import sys

        record = _record("Fundamentals refresh failed", exc_info=sys.exc_info())
    out = _render(record)
    assert SECRET not in out, "API key leaked through the traceback"
    assert "REDACTED" in out


def test_non_secret_query_params_survive():
    out = _render(_record("GET https://example.com/x?symbol=IPST&limit=250"))
    assert "symbol=IPST" in out
    assert "limit=250" in out
