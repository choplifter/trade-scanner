"""OCC option symbols, e.g. SPY251219C00650000.

Parsed from the right end, because that is the only unambiguous direction:
the last 8 characters are the strike in thousandths, the one before is
C/P, the six before that are YYMMDD, and whatever is left is the root --
which may itself contain digits after a corporate action (SPY1, AAPL2).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Kind = Literal["call", "put"]

_OCC = re.compile(r"^(?P<root>[A-Z0-9]{1,6})(?P<date>\d{6})(?P<kind>[CP])(?P<strike>\d{8})$")


@dataclass(frozen=True)
class OccSymbol:
    symbol: str
    root: str
    expiry: date
    kind: Kind
    strike: float

    @property
    def underlying(self) -> str:
        """The root with any adjustment digits stripped: SPY1 -> SPY. Good
        enough for grouping; the contracts endpoint carries the authoritative
        underlying_symbol when it matters."""
        return self.root.rstrip("0123456789") or self.root


def parse_occ(symbol: str) -> OccSymbol:
    match = _OCC.match(symbol.strip().upper())
    if match is None:
        raise ValueError(f"Not an OCC option symbol: {symbol!r}")
    try:
        expiry = datetime.strptime(match.group("date"), "%y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"Not an OCC option symbol (bad date): {symbol!r}") from exc
    return OccSymbol(
        symbol=match.group(0),
        root=match.group("root"),
        expiry=expiry,
        kind="call" if match.group("kind") == "C" else "put",
        strike=int(match.group("strike")) / 1000,
    )


def try_parse_occ(symbol: str | None) -> OccSymbol | None:
    if not symbol:
        return None
    try:
        return parse_occ(symbol)
    except ValueError:
        return None


def format_occ(root: str, expiry: date, kind: Kind, strike: float) -> str:
    return f"{root.upper()}{expiry:%y%m%d}{'C' if kind == 'call' else 'P'}{round(strike * 1000):08d}"
