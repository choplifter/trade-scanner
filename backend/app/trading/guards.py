"""The write-path gate, shared by the equity service and the options service.

Which account an order goes to is decided per request (see
app.routers.trading: the same router is mounted once under /api/trading for
the paper account and once under /api/trading/live), and the gate is where
"live" becomes expensive on purpose:

- TRADING_ENABLED must be on (as before, for both accounts).
- The primary account must be a paper account (as before -- ALPACA_PAPER is
  a one-character edit and must never double as the live switch).
- A live write additionally needs TRADING_ALLOW_LIVE=true *and* the typed
  confirmation the UI collects on every live dialog, sent as the
  X-Live-Confirm header. The header check is not decoration: it means no
  request that merely reached the live prefix by accident (a stale tab, a
  wrong tradingPath rewrite) can place a real order.

Ceilings are per account too. A fat-finger on paper costs nothing; on the
real account it costs exactly the ceiling, so the live ones are far lower.
"""

from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings
from app.trading.errors import LiveConfirmationRequired, LiveTradingRefused, TradingDisabled

Account = Literal["paper", "live"]

# What the user has to type, and the header it travels in.
LIVE_CONFIRMATION = "LIVE"
LIVE_CONFIRM_HEADER = "X-Live-Confirm"


def assert_can_trade(
    settings: Settings,
    account: Account = "paper",
    confirm: str | None = None,
    *,
    live_available: bool | None = None,
) -> None:
    """Every write path starts here. Order matters: the message should name
    the switch the user actually controls, so the master switch comes first
    and the live checks last.

    `live_available` is whether *this user* has a live key pair (their own,
    or the operator's for the admin -- see app.broker.resolver); None keeps
    the old answer from .env alone."""
    if not settings.trading_enabled:
        raise TradingDisabled(
            "Trading is switched off. Set TRADING_ENABLED=true in backend/.env to enable it."
        )
    if not settings.alpaca_paper:
        raise LiveTradingRefused(
            "Refusing to place an order against a live account. This build is "
            "paper-only: ALPACA_PAPER must stay true, and the real account is "
            "reached only through the live prefix with its own keys."
        )
    if account == "live":
        has_live = settings.has_live_credentials if live_available is None else live_available
        if not has_live:
            raise LiveTradingRefused(
                "No live account connected. Enter your live Alpaca keys in Settings → Broker "
                "(the admin may set ALPACA_LIVE_API_KEY_ID / ALPACA_LIVE_API_SECRET_KEY in backend/.env)."
            )
        if not settings.trading_allow_live:
            raise LiveTradingRefused(
                "Live trading is switched off. Set TRADING_ALLOW_LIVE=true in backend/.env "
                "to allow real-money orders."
            )
        if (confirm or "").strip() != LIVE_CONFIRMATION:
            raise LiveConfirmationRequired(
                f'Type {LIVE_CONFIRMATION} to confirm a real-money action.', field="confirm"
            )


@dataclass(frozen=True)
class Limits:
    account: str
    max_order_qty: int
    max_order_notional: float
    max_order_notional_pct: float
    max_option_contracts: int

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "max_order_qty": self.max_order_qty,
            "max_order_notional": self.max_order_notional,
            "max_order_notional_pct": self.max_order_notional_pct,
            "max_option_contracts": self.max_option_contracts,
        }


def limits_for(settings: Settings, account: Account = "paper") -> Limits:
    if account == "live":
        return Limits(
            account="live",
            max_order_qty=settings.trading_live_max_order_qty,
            max_order_notional=settings.trading_live_max_order_notional,
            max_order_notional_pct=settings.trading_live_max_order_notional_pct,
            max_option_contracts=settings.trading_live_max_option_contracts,
        )
    return Limits(
        account="paper",
        max_order_qty=settings.trading_max_order_qty,
        max_order_notional=settings.trading_max_order_notional,
        max_order_notional_pct=settings.trading_max_order_notional_pct,
        max_option_contracts=settings.trading_max_option_contracts,
    )


def can_submit(settings: Settings, account: Account = "paper", *, live_available: bool | None = None) -> bool:
    """What the preview reports: whether a submit *could* pass the gate
    (the typed confirmation aside, which is collected at submit time)."""
    if not settings.trading_enabled or not settings.alpaca_paper:
        return False
    if account == "live":
        has_live = settings.has_live_credentials if live_available is None else live_available
        return has_live and settings.trading_allow_live
    return True
