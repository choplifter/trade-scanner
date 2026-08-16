import logging
import re
import sys

# Query-param names whose values must never reach the logs. FMP takes its key
# as a URL query param -- it has no header form -- and httpx logs the full
# request URL at INFO, so without this the key lands in stdout (and in
# whatever captures stdout) on every fundamentals / market-conditions call.
_SECRET_QUERY_PARAMS = (
    "apikey",
    "api_key",
    "access_token",
    "token",
    "password",
    "secret",
)

# Value runs to the next param separator, whitespace, or quote -- httpx's
# format is `HTTP Request: GET https://host/path?apikey=XXX "HTTP/1.1 200 OK"`,
# so the URL is followed by a space and a quote.
_SECRET_RE = re.compile(
    r"\b(" + "|".join(_SECRET_QUERY_PARAMS) + r")=[^&\s\"']+",
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1=REDACTED", text)


def _redact_arg(arg: object) -> object:
    """Redact a single %-format argument, leaving it untouched if it holds no
    secret. httpx passes an httpx.URL object rather than a str, so this can't
    just check isinstance(arg, str)."""
    if isinstance(arg, str):
        return _redact(arg)
    try:
        text = str(arg)
    except Exception:
        return arg
    redacted = _redact(text)
    # Only swap in the string form when redaction actually changed something,
    # so ordinary args keep their own repr/formatting.
    return redacted if redacted != text else arg


class RedactSecretsFilter(logging.Filter):
    """Strips secret query-param values out of every record on a handler.

    Installed on the root handler rather than the httpx logger specifically,
    so it also covers our own log lines that include a request URL (e.g.
    app.fundamentals.finra_short_interest logging the URL it fetched).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "=" in record.msg:
            record.msg = _redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: _redact_arg(value) for key, value in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(arg) for arg in record.args)
        return True


def _install_redaction(handlers: list[logging.Handler]) -> None:
    for handler in handlers:
        if not any(isinstance(f, RedactSecretsFilter) for f in handler.filters):
            handler.addFilter(RedactSecretsFilter())


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        # Something else (uvicorn, a test harness) configured logging first --
        # still make sure secrets can't slip through its handlers.
        _install_redaction(root.handlers)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    handler.addFilter(RedactSecretsFilter())
    root.addHandler(handler)
    root.setLevel(level)
