"""Failure modes that are *expected* outcomes of trading, not bugs.

A broker refusing an order is ordinary: not enough buying power, the symbol
isn't tradable, the market is shut. None of those are exceptional conditions
in the sense a 500 implies, so they get their own types and are reported to
the caller with a reason it can render, following the structured-422 shape
app.routers.screener already uses for a backtest it can't run.
"""


class TradingError(Exception):
    """Base for everything this package raises deliberately."""

    code = "trading_error"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field

    def to_detail(self) -> dict:
        """The structured body a router puts in HTTPException(detail=...)."""
        return {"code": self.code, "message": self.message, "field": self.field}


class LiveTradingRefused(TradingError):
    """The account is not a paper account, so no order will be placed.

    Deliberately not configurable away with a single flag. ALPACA_PAPER
    controls which Alpaca endpoint the client talks to, and flipping it is a
    one-character edit; it must not double as the only thing standing between
    this app and real money.
    """

    code = "live_trading_refused"


class OrderRejected(TradingError):
    """The broker, or our own pre-flight limits, said no."""

    code = "order_rejected"


class TradingDisabled(TradingError):
    """The feature is switched off in settings (the default)."""

    code = "trading_disabled"
