"""Turns a GexReading into a short, rule-based playbook -- the GEX-specific
counterpart to app.market_data.market_conditions' red/yellow/green readout.

Deliberately descriptive, not advisory, same non-directive framing as
market_conditions.py and app.ai.trade_ideas' system prompt: this states what
the standard GEX heuristics say a regime like this *tends* to mean, not
"buy" or "sell". `regime` is purely the sign of net_gex -- no magnitude
thresholds are invented here, unlike market_conditions.py's VIX/breadth
bands, because there's no widely-agreed "how big is big" for a raw dollar
gamma number across two differently-sized underlyings (SPY vs QQQ).
"""

from dataclasses import dataclass, field

from app.market_data.gamma_exposure import GexReading, StrikeGex, call_wall, gamma_flip_strike, put_wall

# How close spot needs to be to the (approximate) gamma-flip strike before
# flagging "near flip" -- a proximity measure, not a magnitude guess, so this
# is on firmer ground than an absolute dollar threshold would be.
_NEAR_FLIP_PCT = 0.005


@dataclass
class GexPlan:
    symbol: str
    regime: str  # "positive" | "negative" -- sign of net_gex
    near_flip: bool
    gamma_flip_strike: float | None
    call_wall: StrikeGex | None
    put_wall: StrikeGex | None
    playbook: list[str] = field(default_factory=list)


def compute_gex_plan(reading: GexReading) -> GexPlan:
    flip = gamma_flip_strike(reading.by_strike)
    wall_call = call_wall(reading.by_strike)
    wall_put = put_wall(reading.by_strike)
    regime = "positive" if reading.net_gex >= 0 else "negative"
    near_flip = flip is not None and abs(reading.spot_price - flip) / reading.spot_price < _NEAR_FLIP_PCT

    playbook: list[str] = []
    if regime == "negative":
        playbook.append(
            "Dealers are net short gamma: their hedging tends to amplify moves in "
            "whichever direction price starts -- selling into drops, buying into rallies."
        )
        playbook.append(
            "Favor breakout/momentum entries over fading extremes; a stop sized for a "
            "calmer regime can get clipped by the wider swings this regime produces."
        )
    else:
        playbook.append(
            "Dealers are net long gamma: their hedging tends to dampen moves -- price "
            "action leans range-bound, often pinning toward the largest strikes."
        )
        playbook.append(
            "Favor fading extremes near the walls below over chasing breakouts; tighter "
            "stops tend to work better in this regime."
        )

    if wall_call is not None:
        playbook.append(
            f"Call wall at {wall_call.strike:g}: the nearest concentration of call-side "
            "gamma, conventionally read as overhead resistance / a profit-take zone."
        )
    if wall_put is not None:
        playbook.append(
            f"Put wall at {wall_put.strike:g}: the nearest concentration of put-side "
            "gamma, conventionally read as support."
        )
    if near_flip and flip is not None:
        playbook.append(
            f"Spot is within {_NEAR_FLIP_PCT:.1%} of the approximate gamma-flip level "
            f"({flip:.2f}) -- the regime above can flip quickly here, so treat it with "
            "extra caution."
        )

    return GexPlan(
        symbol=reading.symbol,
        regime=regime,
        near_flip=near_flip,
        gamma_flip_strike=flip,
        call_wall=wall_call,
        put_wall=wall_put,
        playbook=playbook,
    )
