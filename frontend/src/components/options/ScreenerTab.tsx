import { useEffect, useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { latestScreen, screenUnderlyings } from "../../api/options";
import { useWatchlist } from "../../hooks/useWatchlist";
import type {
  LoadableStructure,
  OptimizerOutlook,
  ScreenOutcome,
  ScreenResponse,
  ScreenRow,
  ScreenShortLeg,
  ScreenStrategy,
  ScreenVertical,
  Strategy,
} from "../../types/options";
import { formatMoney, formatPrice } from "../../utils/format";
import { ScreenerHelp } from "./ScreenerHelp";
import { requestOptimizer } from "./optimizerIntent";
import { requestTicket } from "./ticketIntent";

interface ScreenerTabProps {
  /** Clicking a row loads that symbol into the widget (and the chart). */
  onSelectSymbol?: (symbol: string) => void;
}

const STRATEGIES: { key: ScreenStrategy; label: string; title: string }[] = [
  {
    key: "cash_secured_put",
    label: "Cash-sec. put",
    title: "Wants implied volatility rich against realised, deep open interest and tight quotes at a 0.10-0.20 delta put.",
  },
  {
    key: "covered_call",
    label: "Covered call",
    title: "The same chain qualities, read off the call side.",
  },
  {
    key: "credit_spread",
    label: "Credit spread",
    title: "As above, and the chain must list a strike further out to buy as the wing -- with enough credit against the width it risks.",
  },
  {
    key: "iron_condor",
    label: "Iron condor",
    title: "A wing on both sides, and both verticals paying enough against their width.",
  },
  {
    key: "debit_spread",
    label: "Debit spread",
    title: "Wants implied volatility cheap against realised, and a wing to sell against the long leg.",
  },
  {
    key: "long_option",
    label: "Long option",
    title: "Cheap implied volatility and quotes tight enough that the debit is not the spread.",
  },
  {
    key: "calendar",
    label: "Calendar",
    title: "Wants the front expiry's implied volatility above the back one's -- the only screen that costs a second chain fetch per symbol.",
  },
];

/**
 * The structure the row's own numbers already describe, ready for the
 * ticket -- the screener found the expiry and the strikes, so sending the
 * reader back to the chain to re-pick them by hand would be busywork.
 *
 * Null where the row does not name a shape: a long option or a debit
 * spread has no short strike to build from (the screen judged the chain,
 * not a direction), and a calendar's second leg is in another expiry the
 * row carries only as a date. Those rows offer the chain instead.
 */
export function structureOf(row: ScreenRow, strategy: ScreenStrategy): LoadableStructure | null {
  if (!row.expiry) return null;
  const base = { underlying: row.symbol, expiry: row.expiry, qty: 1 };
  const put = row.short_put;
  const call = row.short_call;
  if (strategy === "cash_secured_put" && put) {
    return { strategy: "cash_secured_put" as Strategy, ticket: { ...base, strategy: "cash_secured_put" as Strategy, short_strike: put.strike } };
  }
  if (strategy === "covered_call" && call) {
    return { strategy: "covered_call" as Strategy, ticket: { ...base, strategy: "covered_call" as Strategy, short_strike: call.strike } };
  }
  if (strategy === "credit_spread" && row.put_spread) {
    // A put vertical written below the money: bullish, hence bull_put.
    return {
      strategy: "bull_put" as Strategy,
      ticket: {
        ...base,
        strategy: "bull_put" as Strategy,
        long_strike: row.put_spread.long_strike,
        short_strike: row.put_spread.short_strike,
      },
    };
  }
  if (strategy === "iron_condor" && row.put_spread && row.call_spread) {
    return {
      strategy: "iron_condor" as Strategy,
      ticket: {
        ...base,
        strategy: "iron_condor" as Strategy,
        put_long_strike: row.put_spread.long_strike,
        put_short_strike: row.put_spread.short_strike,
        call_short_strike: row.call_spread.short_strike,
        call_long_strike: row.call_spread.long_strike,
      },
    };
  }
  return null;
}

/** The Optimizer question this screen's strategy asks: which family to
 * rank, and the view that sets the target. The screened expiry travels
 * with it, so the tab opens on the same horizon the row was judged at
 * rather than on its own default. */
const OPTIMIZER_INTENT: Record<ScreenStrategy, { outlook: OptimizerOutlook; strategies: Strategy[]; income?: boolean }> = {
  // Income structures put up the position itself -- the strike in cash, the
  // hundred shares -- so the Optimizer's spread-sized default budget would
  // drop every candidate as over budget and answer with an empty list.
  cash_secured_put: { outlook: "bullish", strategies: ["cash_secured_put"], income: true },
  covered_call: { outlook: "neutral", strategies: ["covered_call"], income: true },
  // The screen read the put side, so the vertical it ranks is the bull put.
  credit_spread: { outlook: "bullish", strategies: ["bull_put"] },
  iron_condor: { outlook: "neutral", strategies: ["iron_condor"] },
  debit_spread: { outlook: "bullish", strategies: ["bull_call"] },
  long_option: { outlook: "bullish", strategies: ["long_call"] },
  calendar: { outlook: "neutral", strategies: ["calendar"] },
};

function reasonFor(row: ScreenRow): string {
  const parts = [`${row.passed}/${row.scored} criteria`];
  if (row.iv_rv_ratio != null) parts.push(`IV ${row.iv_rv_ratio.toFixed(2)}x realised`);
  if (row.iv_rank != null) parts.push(`IV rank ${row.iv_rank.toFixed(0)} %`);
  return `screen: ${parts.join(" · ")}`;
}

/** A typed bound, or the default when the field is empty or nonsense --
 * a half-typed "0." must not send NaN and be refused by the validator. */
function numeric(text: string, fallback: number): number {
  const value = Number(text);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/** How old a stored run is, in the words a trader uses about a table. */
function ageOf(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

function pct(value: number | null | undefined, digits = 0): string {
  return value == null ? "—" : `${(value * 100).toFixed(digits)} %`;
}

/** The widest quote anywhere in the structure, as a share of its own mid.
 *
 * A package fills at the price of its worst leg, so this one number
 * answers "can this be traded at all" -- the question the help panel says
 * to settle before reading anything else, and the one the table could not
 * be ordered by. Up to four values were spread across two cells and the
 * reader had to find the largest by eye.
 */
function worstQuote(row: ScreenRow): number | null {
  const widths = [
    row.short_put?.spread_fraction,
    row.short_call?.spread_fraction,
    row.put_spread?.wing_spread_fraction,
    row.call_spread?.wing_spread_fraction,
  ].filter((w): w is number => w != null);
  return widths.length ? Math.max(...widths) : null;
}

/** What each sortable column reads off a row. The server ranks by criteria
 * passed, which is the right default and the wrong lens for several real
 * questions: the highest expectancy on one side of a condor sat eight rows
 * down behind structures that merely failed fewer tests, and there was no
 * way to bring it up. Null sorts last whichever direction is chosen -- a
 * row that could not be measured is not the best or the worst of anything.
 */
const SORTS: Record<string, (r: ScreenRow) => number | string | null> = {
  Symbol: (r) => r.symbol,
  Passed: (r) => r.passed,
  "Worst leg": worstQuote,
  IV: (r) => r.atm_iv,
  RV: (r) => r.realised_vol,
  "IV/RV": (r) => r.iv_rv_ratio,
  "IV rank": (r) => r.iv_rank,
  OI: (r) => r.open_interest,
  "Vol/OI": (r) => r.volume_oi_ratio,
  Expiry: (r) => r.dte,
  "Put side": (r) => r.put_outcome?.expected_value ?? null,
  "Call side": (r) => r.call_outcome?.expected_value ?? null,
  "R/R": (r) => r.outcome?.risk_reward ?? null,
  "Loss prob": (r) => r.outcome?.loss_probability ?? null,
  EV: (r) => r.outcome?.expected_value ?? null,
  "Risk %": (r) => r.risk_share,
};

function sortRows(rows: ScreenRow[], by: string | null, desc: boolean): ScreenRow[] {
  const read = by ? SORTS[by] : undefined;
  if (!read) return rows;
  return [...rows].sort((a, b) => {
    const x = read(a);
    const y = read(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    const cmp = typeof x === "string" || typeof y === "string" ? String(x).localeCompare(String(y)) : x - y;
    return desc ? -cmp : cmp;
  });
}

/** A header that sorts when the column has something to sort by. */
function Th({
  label,
  title,
  sort,
  onSort,
}: {
  label: string;
  title?: string;
  sort: { by: string | null; desc: boolean };
  onSort: (label: string) => void;
}) {
  const sortable = label in SORTS;
  const active = sort.by === label;
  return (
    <th
      scope="col"
      title={title}
      aria-sort={active ? (sort.desc ? "descending" : "ascending") : undefined}
      className={sortable ? "screen-sortable" : undefined}
      onClick={sortable ? () => onSort(label) : undefined}
    >
      {label}
      {active && <span aria-hidden> {sort.desc ? "▾" : "▴"}</span>}
    </th>
  );
}

function mark(passed: boolean | null): string {
  return passed === null ? "?" : passed ? "✓" : "✗";
}

/** One row's criteria, folded out on click: every measure with what it
 * found, so a "✗" can always be traced to a number. */
function Criteria({ row }: { row: ScreenRow }) {
  return (
    <ul className="screen-criteria">
      {row.criteria.map((c) => (
        <li key={c.key} className={c.passed === null ? "unknown" : c.passed ? "pass" : "fail"}>
          <span className="screen-mark">{mark(c.passed)}</span> <strong>{c.label}:</strong> {c.detail}
        </li>
      ))}
      {row.note && <li className="unknown">{row.note}</li>}
    </ul>
  );
}

/**
 * Which underlyings suit a strategy -- the Optimizer's question one level
 * up (backend app/options/screener.py). The symbols are the watchlist:
 * every row costs a chain fetch, so this screens a list you already keep
 * rather than a universe.
 */
/** One quote width, marked when crossing it costs more than the screen
 * allows. Credit, expectancy and risk-reward are all computed from mids,
 * so a leg quoted 0.21 against 1.31 prices the structure off a number
 * nobody is offering -- this is the value to read before any of them. */
function Width({ fraction, maxSpread, what }: { fraction: number | null; maxSpread: number; what: string }) {
  if (fraction == null) return <>—</>;
  const wide = fraction > maxSpread;
  return (
    <span
      className={wide ? "delta-down" : undefined}
      title={
        wide
          ? `Crossing the ${what}'s quote costs ${pct(fraction)} of its mid, past the ${pct(maxSpread)} this screen allows -- the credit beside it is a mid nobody is offering.`
          : `What crossing the ${what}'s quote costs, as a share of its mid.`
      }
    >
      {pct(fraction)}
    </span>
  );
}

/** Everything about one side of the structure in one cell: the strikes,
 * the width between them, the short's delta, what crossing each of the two
 * quotes costs, and what comes back per dollar of width.
 *
 * It was three columns -- short put, short call, and a single wing -- which
 * repeated the short strike and left two things out. The wing of an iron
 * condor was printed for whichever side won a coin toss, so the row never
 * held all four legs; and the wing's own quote width was in the data and in
 * none of the columns, although it is the leg furthest out of the money and
 * routinely the wider of the two. Measured on 2026-10-01: GOOGL's short put
 * quoted 23 % against 92 % on the wing behind it, NVDA's 41 % against 169 %.
 * The number on screen was the harmless one of the pair. */
function SideCell({
  leg,
  vertical,
  outcome,
  maxSpread,
  side,
}: {
  leg: ScreenShortLeg | null;
  vertical: ScreenVertical | null;
  outcome: ScreenOutcome | null;
  maxSpread: number;
  side: "put" | "call";
}) {
  if (!leg) return <td>—</td>;
  return (
    <td>
      {vertical ? (
        <>
          {formatPrice(vertical.long_strike)}/{formatPrice(vertical.short_strike)} · {vertical.width}w
        </>
      ) : (
        formatPrice(leg.strike)
      )}{" "}
      · Δ{Math.abs(leg.delta).toFixed(2)} ·{" "}
      <Width fraction={leg.spread_fraction} maxSpread={maxSpread} what={`short ${side}`} />
      {vertical && (
        <>
          /<Width fraction={vertical.wing_spread_fraction} maxSpread={maxSpread} what="wing" />
        </>
      )}
      {vertical && (
        <span title="Credit taken in per dollar of width risked. The screen wants at least 10 %.">
          {" · "}
          {pct(vertical.credit_to_width)}
        </span>
      )}
      {outcome && (
        <span
          className={outcome.expected_value > 0 ? "delta-up" : "delta-down"}
          title={`This side valued under the volatility its own strikes trade at. Not half of the EV column: that is one expectation over one distribution, while this says whether this ${side} spread is priced above or below what its own corner of the smile implies. It is usually the two together that explain the structure -- an equity skew makes the put wing dearer in volatility than the short beside it, and the call side rarely gives up as much.`}
        >
          {" · "}
          {formatMoney(outcome.expected_value)}
        </span>
      )}
    </td>
  );
}


export function ScreenerTab({ onSelectSymbol }: ScreenerTabProps) {
  const { symbols } = useWatchlist();
  const [strategy, setStrategy] = useState<ScreenStrategy>("iron_condor");
  const [result, setResult] = useState<ScreenResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  // Watchlist, or the whole tradable universe narrowed by stage one. The
  // universe by default: the watchlist answers "is this one worth it",
  // the universe answers "where is there anything worth it at all", and
  // the second is the question a screener exists for.
  const [universe, setUniverse] = useState(true);
  // The screen's own bounds. The backend has always taken them; until now
  // the tab sent none, so every run was the 30-60 day default -- and on
  // this market that was the difference between +1.54 and +14.10 of
  // expected value, since a 51-day expiry sits behind the whole earnings
  // season and a 16-day one in front of it.
  const [dteMin, setDteMin] = useState("30");
  const [dteMax, setDteMax] = useState("60");
  const [deltaMin, setDeltaMin] = useState("0.10");
  const [deltaMax, setDeltaMax] = useState("0.20");
  const [minOi, setMinOi] = useState("5000");
  // How wide the bought wing sits from the short, in points. Blank leaves
  // it at the backend's 3 % of spot. It is worth having at hand because
  // the width *is* the risk of the vertical: on a 78-dollar ETF the
  // default lands on a one-point wing, 22.50 of profit against 77.50, and
  // eight legs of commission then decide the expectancy.
  const [wingPoints, setWingPoints] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);
  // Null keeps the server's ranking, which is the sensible default; a
  // click takes over from it. Reset whenever a new run arrives, so a sort
  // chosen for one screen does not silently govern the next.
  const [sort, setSort] = useState<{ by: string | null; desc: boolean }>({ by: null, desc: true });
  const onSort = (label: string) =>
    setSort((s) => (s.by === label ? { by: label, desc: !s.desc } : { by: label, desc: true }));
  const [maxSpread, setMaxSpread] = useState("10");
  // Not a checkbox: for a short premium structure *when* the report falls
  // decides everything. Early is the crush arriving while the strikes are
  // still far away; late is a gap meeting high gamma and no time left.
  const [earnings, setEarnings] = useState<"avoid" | "early" | "ignore">("early");

  // The background pass writes one table per strategy every half hour
  // (backend app/options/screen_job.py). Showing it on open is the whole
  // point: a universe screen is a dozen seconds, and nobody should spend
  // them to find out what the market looked like four minutes ago.
  useEffect(() => {
    let cancelled = false;
    setResult(null);
    latestScreen(strategy)
      .then((res) => {
        if (!cancelled && res.screen) setResult(res.screen);
      })
      .catch(() => {
        // No stored run is the ordinary case before the first pass.
      });
    return () => {
      cancelled = true;
    };
  }, [strategy]);

  const run = async () => {
    if (!universe && symbols.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const bounds = {
        strategy,
        dte_min: numeric(dteMin, 30),
        dte_max: numeric(dteMax, 60),
        short_delta_min: numeric(deltaMin, 0.1),
        short_delta_max: numeric(deltaMax, 0.2),
        min_open_interest: Math.round(numeric(minOi, 5000)),
        // Typed as a percentage, sent as the fraction the backend wants.
        max_spread_fraction: numeric(maxSpread, 10) / 100,
        earnings_policy: earnings,
        ...(wingPoints.trim() === "" ? {} : { wing_points: numeric(wingPoints, 0) }),
      };
      setResult(
        await screenUnderlyings(
          universe ? { ...bounds, scan_universe: true, limit: 40 } : { ...bounds, symbols: symbols.slice(0, 60) },
        ),
      );
      // A new run comes back in the server's own ranking; a sort chosen for
      // the last one should not quietly govern this one.
      setSort({ by: null, desc: true });
    } catch (err: unknown) {
      setError(err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="opt-screener">
      <div className="opt-preference">
        {STRATEGIES.map((b) => (
          <button
            key={b.key}
            type="button"
            className="timeframe-button"
            aria-pressed={strategy === b.key}
            onClick={() => setStrategy(b.key)}
            title={b.title}
          >
            {b.label}
          </button>
        ))}
        <button
          type="button"
          className="timeframe-button"
          aria-pressed={universe}
          onClick={() => setUniverse((v) => !v)}
          title="Screen the tradable universe instead of the watchlist. Stage one narrows it on price and dollar volume -- numbers the scanner's universe already carries, so that part is free -- and only the survivors cost a chain fetch."
        >
          {universe ? "Universe" : "Watchlist"}
        </button>
        <button
          type="button"
          className="generate-button opt-run"
          disabled={loading || (!universe && symbols.length === 0)}
          onClick={() => void run()}
        >
          {loading ? "Reading chains…" : universe ? "Screen the universe" : `Screen ${Math.min(symbols.length, 60)} symbols`}
        </button>
        <span className="order-hint">
          {universe ? "top 40 by dollar volume · one chain fetch each" : "from your watchlist · one chain fetch each"}
        </span>
        <button
          type="button"
          className="timeframe-button options-help-button"
          onClick={() => setHelpOpen(true)}
          title="How to read a screen, and what to try when nothing passes"
          aria-label="How to read a screen"
        >
          ?
        </button>
      </div>
      <ScreenerHelp open={helpOpen} onClose={() => setHelpOpen(false)} />

      <div className="opt-preference screen-filters">
        <label title="Days to expiry. The screen takes the listed expiry nearest the middle of this window; past 60 days it asks for the full expiry board.">
          DTE{" "}
          <input type="number" min={1} max={400} step={1} value={dteMin} onChange={(e) => setDteMin(e.target.value)} />
          <span aria-hidden>–</span>
          <input type="number" min={1} max={400} step={1} value={dteMax} onChange={(e) => setDteMax(e.target.value)} />
        </label>
        <label title="The band the short strike's delta must fall in. 0.10-0.20 is the conventional premium-selling range; higher takes more premium and more risk.">
          Δ{" "}
          <input type="number" min={0.01} max={0.99} step={0.01} value={deltaMin} onChange={(e) => setDeltaMin(e.target.value)} />
          <span aria-hidden>–</span>
          <input type="number" min={0.01} max={0.99} step={0.01} value={deltaMax} onChange={(e) => setDeltaMax(e.target.value)} />
        </label>
        <label title="Open interest across the screened expiry's fetched strikes -- one expiry's, an order of magnitude below a whole-chain number.">
          OI{" "}
          <input type="number" min={0} step={500} value={minOi} onChange={(e) => setMinOi(e.target.value)} />
        </label>
        <label title="How far the bought wing sits from the short strike, in points. Blank aims at 3 % of spot, which on a cheap ETF rounds down to a one-point wing. The width is the whole risk of the vertical, so widening it changes the credit-to-width ratio and the expectancy with it.">
          Wing{" "}
          <input
            type="number"
            min={0.5}
            step={0.5}
            placeholder="3 %"
            value={wingPoints}
            onChange={(e) => setWingPoints(e.target.value)}
          />
        </label>
        <label title="How wide the quote at the wider short leg may be, as a percentage of its mid.">
          Quote ≤{" "}
          <input type="number" min={1} max={100} step={1} value={maxSpread} onChange={(e) => setMaxSpread(e.target.value)} />
          <span aria-hidden> %</span>
        </label>
        <label title="What a report inside the expiry means. Early: one in the first third of the position's life passes -- the implied volatility that made the credit fat collapses while the strikes are still far away, and weeks of decay follow. Avoid: any report inside fails. Ignore: reported, not judged.">
          Earnings{" "}
          <select value={earnings} onChange={(e) => setEarnings(e.target.value as "avoid" | "early" | "ignore")}>
            <option value="early">early ok</option>
            <option value="avoid">avoid</option>
            <option value="ignore">ignore</option>
          </select>
        </label>
      </div>

      {error && <p className="order-rejection">{error}</p>}
      {!universe && symbols.length === 0 && <p className="widget-empty">Add symbols to the watchlist to screen them.</p>}

      {result?.stored_at && (
        <p className="order-hint">
          Stored run from {new Date(result.stored_at).toLocaleTimeString()} ({ageOf(result.stored_at)}) · the background
          pass screens the universe every half hour. Press the button for a live one.
        </p>
      )}
      {result && (
        <>
          {result.preselection && (
            <p className="order-hint">
              Stage one: {result.preselection.considered.toLocaleString()} symbols considered,{" "}
              {result.preselection.selected} priced
              {Object.keys(result.preselection.dropped).length > 0
                ? ` · dropped ${Object.entries(result.preselection.dropped)
                    .map(([reason, n]) => `${n} ${reason.replace(/_/g, " ")}`)
                    .join(", ")}`
                : ""}
            </p>
          )}
          <p className="order-hint">
            {result.criteria.dte[0]}–{result.criteria.dte[1]} DTE · OI {result.criteria.min_open_interest.toLocaleString()}+ ·
            quotes under {pct(result.criteria.max_spread_fraction)} · short delta {result.criteria.short_delta[0].toFixed(2)}–
            {result.criteria.short_delta[1].toFixed(2)}
            {result.criteria.earnings_policy === "avoid"
              ? " · no earnings inside"
              : result.criteria.earnings_policy === "early"
                ? ` · earnings only in the first ${Math.round(result.criteria.early_earnings_fraction * 100)} % of the position`
                : ""}
          </p>
          <table className="opt-table screen-table">
            <thead>
              <tr>
                <Th label="Symbol" sort={sort} onSort={onSort} />
                <Th label="Passed" title="Criteria met out of those that could be judged. The server's own ranking, and the default order." sort={sort} onSort={onSort} />
                <Th
                  label="Worst leg"
                  title="The widest quote anywhere in the structure, as a share of its own mid. A package fills at the price of its worst leg, so sorting this ascending puts what can actually be traded on top -- which is the question to settle before reading an expectancy. Red past the limit set above."
                  sort={sort}
                  onSort={onSort}
                />
                <Th label="IV" title="At-the-money implied volatility of the screened expiry." sort={sort} onSort={onSort} />
                <Th label="RV" title="Close-to-close volatility of the last 20 sessions, annualised." sort={sort} onSort={onSort} />
                <Th label="IV/RV" title="Implied over realised. Above 1.2 the premium is rich, below 0.95 it is cheap." sort={sort} onSort={onSort} />
                <Th label="IV rank" title="Where today's IV sits in this symbol's own recorded range. Needs 20 sessions." sort={sort} onSort={onSort} />
                <Th label="OI" title="Open interest across the fetched strikes of that expiry." sort={sort} onSort={onSort} />
                <Th
                  label="Vol/OI"
                  title="Contracts traded today against the positions already open. Open interest says a crowd is positioned; this says whether anyone is still trading it. Above 1 the expiry is being built today rather than carried."
                  sort={sort}
                  onSort={onSort}
                />
                <Th label="Expiry" title="Sorts by days to expiry." sort={sort} onSort={onSort} />
                <Th
                  label="Put side"
                  title="long/short · width · delta of the short · what crossing the short's quote costs / what crossing the wing's costs · credit per dollar of width · what this side is worth under the volatility its own strikes trade at. The two quote widths are the ones to read first: everything priced after them is computed from mids, so a wide quote makes the rest fiction. Either is marked red past the limit set above. Sorts by that side's own expectancy."
                  sort={sort}
                  onSort={onSort}
                />
                <Th label="Call side" title="As the put side, read off the calls. Sorts by that side's own expectancy." sort={sort} onSort={onSort} />
                {result.rows.some((r) => r.outcome) && (
                  <>
                    <th scope="col" title="Max profit and max loss of the structure named in this row, per contract.">
                      Profit / Loss
                    </th>
                    <Th label="R/R" title="What the structure risks against what it can make. 3:1 means three dollars at risk for every one it pays." sort={sort} onSort={onSort} />
                    <Th label="Loss prob" title="Share of the option market's own implied distribution at expiry under which the structure loses." sort={sort} onSort={onSort} />
                    <Th
                      label="EV"
                      title="The payoff weighted by that distribution, over one distribution built from the at-the-money volatility. A structure can pay a large credit, lose rarely, and still be negative here -- which is what a list sorted by max profit hides."
                      sort={sort}
                      onSort={onSort}
                    />
                    <Th
                      label="Risk %"
                      title="The max loss against your account's equity -- the size decision, as opposed to R/R, which is the structure's own shape. The criterion fails past 2 %."
                      sort={sort}
                      onSort={onSort}
                    />
                  </>
                )}
                {result.rows.some((r) => r.term_ratio != null) && (
                  <th scope="col" title="The front expiry's implied volatility over the back one's.">Front/back</th>
                )}
                <th scope="col" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {sortRows(result.rows, sort.by, sort.desc).map((row) => (
                <>
                  <tr
                    key={row.symbol}
                    className={`screen-row${open === row.symbol ? " open" : ""}`}
                    onClick={() => setOpen(open === row.symbol ? null : row.symbol)}
                  >
                    <th scope="row">
                      <button
                        type="button"
                        className="link-button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectSymbol?.(row.symbol);
                        }}
                        title="Load this symbol's chain"
                      >
                        {row.symbol}
                      </button>
                    </th>
                    <td className={row.passed === row.scored ? "delta-up" : undefined}>
                      {row.passed}/{row.scored}
                    </td>
                    <td>
                      <Width
                        fraction={worstQuote(row)}
                        maxSpread={result.criteria.max_spread_fraction}
                        what="widest leg of this structure"
                      />
                    </td>
                    <td>{pct(row.atm_iv)}</td>
                    <td>{pct(row.realised_vol)}</td>
                    <td className={row.iv_rv_ratio == null ? undefined : row.iv_rv_ratio >= 1.2 ? "delta-up" : row.iv_rv_ratio <= 0.95 ? "delta-down" : undefined}>
                      {row.iv_rv_ratio == null ? "—" : `${row.iv_rv_ratio.toFixed(2)}×`}
                    </td>
                    <td title={row.iv_rank == null ? `${row.iv_rank_samples} sessions recorded, 20 needed` : undefined}>
                      {row.iv_rank == null ? "—" : `${row.iv_rank.toFixed(0)} %`}
                    </td>
                    <td title={row.open_interest == null ? "Not reported for this expiry right now" : undefined}>
                      {row.open_interest == null ? "—" : row.open_interest.toLocaleString()}
                    </td>
                    <td className={row.volume_oi_ratio != null && row.volume_oi_ratio >= 1 ? "delta-up" : undefined}>
                      {row.volume_oi_ratio == null ? "—" : `${row.volume_oi_ratio.toFixed(2)}×`}
                    </td>
                    <td>{row.expiry ? `${row.expiry} (${row.dte}d)` : "—"}</td>
                    <SideCell
                      leg={row.short_put}
                      vertical={row.put_spread}
                      outcome={row.put_outcome}
                      maxSpread={result.criteria.max_spread_fraction}
                      side="put"
                    />
                    <SideCell
                      leg={row.short_call}
                      vertical={row.call_spread}
                      outcome={row.call_outcome}
                      maxSpread={result.criteria.max_spread_fraction}
                      side="call"
                    />
                    {result.rows.some((r) => r.outcome) && (
                      <>
                        <td>
                          {row.outcome
                            ? `${formatMoney(row.outcome.max_profit ?? 0)} / ${formatMoney(row.outcome.max_loss ?? 0)}`
                            : "—"}
                        </td>
                        <td>{row.outcome?.risk_reward == null ? "—" : `${row.outcome.risk_reward.toFixed(1)} : 1`}</td>
                        <td>{row.outcome ? pct(row.outcome.loss_probability) : "—"}</td>
                        <td className={row.outcome ? (row.outcome.expected_value > 0 ? "delta-up" : "delta-down") : undefined}>
                          {row.outcome ? formatMoney(row.outcome.expected_value) : "—"}
                        </td>
                        <td className={row.risk_share != null && row.risk_share > 0.02 ? "delta-down" : undefined}>
                          {row.risk_share == null ? "—" : pct(row.risk_share, 1)}
                        </td>
                      </>
                    )}
                    {result.rows.some((r) => r.term_ratio != null) && (
                      <td>{row.term_ratio == null ? "—" : `${row.term_ratio.toFixed(2)}×`}</td>
                    )}
                    <td className="screen-actions">
                      {(() => {
                        const structure = structureOf(row, result.strategy);
                        return structure ? (
                          <button
                            type="button"
                            className="timeframe-button"
                            title="Load these strikes into the spread ticket on the Chain tab"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectSymbol?.(row.symbol);
                              requestTicket({ symbol: row.symbol, structure });
                            }}
                          >
                            Ticket
                          </button>
                        ) : null;
                      })()}
                      <button
                        type="button"
                        className="timeframe-button"
                        title="Rank the structures for this symbol in the Optimizer, on the expiry and family this screen used"
                        onClick={(e) => {
                          e.stopPropagation();
                          const intent = OPTIMIZER_INTENT[result.strategy];
                          onSelectSymbol?.(row.symbol);
                          requestOptimizer({
                            symbol: row.symbol,
                            outlook: intent.outlook,
                            reason: reasonFor(row),
                            request: {
                              strategies: intent.strategies,
                              ...(intent.income ? { budget: null } : {}),
                              ...(row.expiry ? { horizon_expiry: row.expiry } : {}),
                            },
                          });
                        }}
                      >
                        Optimize
                      </button>
                    </td>
                  </tr>
                  {open === row.symbol && (
                    <tr key={`${row.symbol}-criteria`} className="screen-detail">
                      <td colSpan={18}>
                        <Criteria row={row} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
          <p className="order-hint">{result.disclaimer}</p>
        </>
      )}
    </div>
  );
}
