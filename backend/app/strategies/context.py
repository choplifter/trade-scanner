"""What a strategy script is handed, and what it may hand back.

Mirrors app.indicators.context: a strategy file is given a context and asked
for an answer, never asked to fetch anything or to decide whether it should
have been called. The difference is what the answer is worth -- an indicator
returns numbers to draw, a strategy returns a trade, so Signal validates
itself rather than trusting the file that built it.

The app already shares a *decision* between the two sides: formulas.
is_momentum_alert is literally called by the live cache and by
momentum_backtest, not reimplemented. That caught the class of bug where two
copies of a rule drift apart. It did not catch the next class down, because
each side still derives the rule's *inputs* for itself -- which is how
momentum_backtest's trailing window silently lost all its slack the moment
MOMENTUM_WINDOW changed from 15 to 30 minutes (see _TRAILING_WINDOW_BARS
there, and the comment explaining what it cost).

So what is shared here is not just evaluate() but build_context(): the
session slice, the session gate and the VWAP reading are derived once, in
one place, and both callers receive the same object. A strategy sees a
StrategyContext and nothing else -- it cannot reach for a bar list of its
own and slice it differently.

Pure and network-free, so every rule can be tested against hand-built bars.
"""

from dataclasses import dataclass

from app.market_data.momentum import is_regular_session_bar, same_trading_day
from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT, STOP_ON_TOUCH


def side_sign(side: str) -> int:
    """+1 for a long, -1 for a short -- the factor that lets one comparison
    serve both directions instead of every rule writing each twice."""
    return 1 if side == SIDE_LONG else -1


def next_level(levels, entry: float, sign: int) -> float | None:
    """The nearest level ahead of the entry, or None if there is none.

    "Ahead" is direction-relative: above for a long, below for a short.

    Here rather than in each strategy because "the target is the next level"
    is one decision several rules share, and it existed as two private
    copies before the third rule wanting it forced the question. Copies that
    look alike are the state that rots -- see strategies.breakout's docstring
    for the fuller argument.
    """
    ahead = [level for level in levels if sign * (level - entry) > 0]
    if not ahead:
        return None
    return min(ahead, key=lambda level: sign * (level - entry))


@dataclass(frozen=True)
class StrategyContext:
    """Everything a strategy is allowed to look at, at one bar.

    session_bars ends with `bar` itself and starts at the first bar of the
    same ET date -- same-day premarket included. That inclusion is
    deliberate and matches momentum.same_trading_day: premarket is a valid
    *reference* (it is what makes an opening range measurable at 09:35),
    while only a regular-session bar may *trigger*, which is why
    build_context refuses to build one otherwise.
    """

    symbol: str
    bar: object
    session_bars: list
    # The running regular-session VWAP at each bar of session_bars, same
    # length and same order, so session_vwaps[i] is the line as it stood when
    # session_bars[i] closed. None wherever nothing had traded in the session
    # yet.
    #
    # A list rather than only the latest value because a rule that asks
    # whether price has *respected* a line has to look at where the line was
    # at each earlier test, not where it is now. Handed over rather than left
    # to the strategy to recompute: VWAP is a cumulative accumulator, and a
    # second one built inside a strategy file would drift from the one the
    # chart draws -- which is the drift this whole context exists to prevent.
    session_vwaps: list
    # The same figure anchored at the premarket open. Both are carried
    # because on a gapper they disagree about which side of the line price is
    # on (see app.market_data.vwap's docstring for the case that forced this).
    premarket_vwap: float | None
    # Horizontal levels price keeps returning to, ascending, from
    # app.market_data.levels. Measured on hourly bars and from history that
    # had already printed -- never recomputed per bar, both because the scan
    # is expensive and because a level is something marked from completed
    # structure, not something that appears mid-session.
    #
    # Empty is normal: a fresh listing, or a symbol with no level clearing the
    # visit floor, simply has none. A rule that targets a level must treat
    # that as "no trade", not as "target zero".
    levels: tuple = ()
    # The average daily true range over recent completed sessions, from
    # app.market_data.atr, computed by the walk that owns the bar history --
    # a strategy sees session_bars only and cannot reach the prior days an
    # ATR needs, which is the same reason levels arrive precomputed.
    #
    # None means *unknowable*, not zero: the live scanner's cache holds one
    # prior session, a fresh listing holds none. A rule using this as a veto
    # decides for itself what None means and documents the choice -- see the
    # opening-range rule, which lets an uncomputable veto decline rather
    # than silently unlisting the setup everywhere ATR is out of reach.
    daily_atr: float | None = None

    @property
    def vwap(self) -> float | None:
        """The line as of this bar -- the last of session_vwaps."""
        return self.session_vwaps[-1] if self.session_vwaps else None


@dataclass(frozen=True)
class Signal:
    """A rule that fired, with the three prices that make it a trade.

    Stops here are *places*, not percentages: under the opening range, under
    VWAP, under the flag low. That is the form the strategies are written
    in, and it is also what the ticket needs. The conversion to the
    percentage exit_rules.ExitRule wants happens in stop_pct below, once,
    rather than at each call site.

    Both directions. Most signals in this app are long-only for a real
    reason -- is_momentum_alert's confirmation (green candle, above VWAP) has
    no sign-flip -- but a rule that reads a level for rejection works
    identically from either side of it, so a strategy declares its own.

    A strategy also declares how its position is *managed*, because that is
    part of the rule and not a property of the backtest: whether the stop
    fires on a touch or only on a close through the level, and whether part
    of the position comes off at the target with the rest trailed to
    break-even. See exit_rules for what each one does to the result.
    """

    strategy: str
    entry_price: float
    stop_price: float
    target_price: float
    reason: str
    side: str = SIDE_LONG
    stop_trigger: str = STOP_ON_TOUCH
    scale_out: float = 0.0

    def __post_init__(self) -> None:
        # Raised, not silently dropped. A stop on the wrong side of the entry
        # is a bug in the rule that produced it, and ExitRule would refuse it
        # one layer later anyway (stop_pct is the denominator of R) -- failing
        # here names the strategy instead of the backtest.
        if self.side not in (SIDE_LONG, SIDE_SHORT):
            raise ValueError(f"{self.strategy}: side must be {SIDE_LONG!r} or {SIDE_SHORT!r}.")
        if self.entry_price <= 0:
            raise ValueError(f"{self.strategy}: entry_price must be positive.")
        if self.risk_per_share <= 0:
            beyond = "below" if self.side == SIDE_LONG else "above"
            raise ValueError(
                f"{self.strategy}: stop {self.stop_price} is not {beyond} entry "
                f"{self.entry_price} for a {self.side}."
            )
        if self.sign * (self.target_price - self.entry_price) <= 0:
            beyond = "above" if self.side == SIDE_LONG else "below"
            raise ValueError(
                f"{self.strategy}: target {self.target_price} is not {beyond} entry "
                f"{self.entry_price} for a {self.side}."
            )

    @property
    def sign(self) -> int:
        return side_sign(self.side)

    @property
    def risk_per_share(self) -> float:
        """The distance to the stop -- the amount actually at risk, and the
        unit both exit_rules (R) and trading.sizing.shares_for_risk speak.
        Sharing it is what makes a backtest R and a live position size
        describe the same trade.
        """
        return self.sign * (self.entry_price - self.stop_price)

    @property
    def stop_pct(self) -> float:
        return self.risk_per_share / self.entry_price

    @property
    def reward_ratio(self) -> float:
        """The target in R. Derived from the structural target rather than
        configured, so a rule that aims at the next level reports the ratio
        that level actually implies instead of a chosen constant.
        """
        return self.sign * (self.target_price - self.entry_price) / self.risk_per_share


def gate_by_vwap_side(ctx: StrategyContext, signal: Signal | None) -> Signal | None:
    """The user's standing rule over every strategy: a signal may only be
    long with price above the session VWAP, and only short below it.

    Applied to whatever evaluate() returned rather than written into each
    rule, so a rule added later cannot forget it. "Price" is the signal
    bar's close -- the same print every rule here enters on -- and a close
    exactly on the line is neither above nor below, so it gates both sides.

    An unknowable line (vwap None) lets the signal stand, the same
    asymmetry the ATR veto documents in opening_range_breakout: a gate that
    cannot be computed refusing everything would unlist every setup exactly
    where nobody can see why. In practice the line exists wherever a signal
    can fire -- only a regular-session bar may trigger, and by then the
    session accumulator has a value.
    """
    if signal is None:
        return None
    vwap = ctx.vwap
    if vwap is None:
        return signal
    if signal.sign * (ctx.bar.close - vwap) <= 0:
        return None
    return signal


def build_context(
    symbol: str,
    bars: list,
    index: int,
    vwaps: list,
    premarket_vwap: float | None = None,
    levels: tuple = (),
    daily_atr: float | None = None,
) -> StrategyContext | None:
    """Assemble the view of the market at bars[index], or None if nothing may
    trigger there.

    Returns None outside the regular session (see
    momentum.is_regular_session_bar: a move measured across a market close
    is a session-boundary artifact, not momentum). Callers treat None as
    "no signal here" and, in the backtest, as a reason to reset streak
    state.

    The session slice is built by walking *backwards* until the ET date
    changes rather than filtering the whole history-so-far. Over a
    multi-week walk the filtering version is O(n^2); this one is bounded by
    the length of one session.

    `vwaps` must be the running VWAP at each bar of `bars`, same length and
    same order. Sliced here alongside the bars so the two cannot fall out of
    step -- an off-by-one between a bar and its VWAP would read as price
    respecting a line it never touched.
    """
    if index < 0 or index >= len(bars):
        return None
    bar = bars[index]
    if not is_regular_session_bar(bar):
        return None

    start = index
    while start > 0 and same_trading_day(bars[start - 1], bar):
        start -= 1

    if len(vwaps) != len(bars):
        raise ValueError(
            f'vwaps has {len(vwaps)} entries for {len(bars)} bars -- they must be aligned.'
        )

    return StrategyContext(
        symbol=symbol,
        bar=bar,
        session_bars=bars[start : index + 1],
        session_vwaps=vwaps[start : index + 1],
        premarket_vwap=premarket_vwap,
        levels=tuple(sorted(levels)),
        daily_atr=daily_atr,
    )
