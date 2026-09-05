import { useEffect, useMemo, useState } from "react";

import type { OptimizerState } from "../../hooks/useOptionsOptimizer";
import {
  STRATEGY_GROUPS,
  type ChainResponse,
  type ExpiryInfo,
  type LoadableStructure,
  type OptimizeRequest,
  type OptimizerResult,
  type Payoff,
  type Strategy,
} from "../../types/options";
import { formatMoney, formatNum, formatPrice } from "../../utils/format";
import { formatExpiry, weekdayOf } from "../../utils/occ";

interface OptimizerTabProps {
  symbol: string | null;
  /** The chain on screen: its spot prefills the target, its at-the-money IV
   * sizes the implied move the outlook buttons work in. */
  chain: ChainResponse | null;
  expiries: ExpiryInfo[];
  optimizer: OptimizerState;
  /** Applies a result's ticket to the widget's own strategy/expiry/legs.
   * Returns false when it could not be loaded, so the card can say so. */
  onLoad: (structure: LoadableStructure) => boolean;
}

type Outlook = "very_bearish" | "bearish" | "neutral" | "directional" | "bullish" | "very_bullish";

/** OptionStrat's six views. `move` is in implied moves: the target sits
 * that many one-sigma moves from the spot (both sides for directional). */
const OUTLOOKS: { key: Outlook; label: string; move: number; tone: "bear" | "flat" | "both" | "bull" }[] = [
  { key: "very_bearish", label: "Very bearish", move: -2, tone: "bear" },
  { key: "bearish", label: "Bearish", move: -1, tone: "bear" },
  { key: "neutral", label: "Neutral", move: 0, tone: "flat" },
  { key: "directional", label: "Directional", move: 1, tone: "both" },
  { key: "bullish", label: "Bullish", move: 1, tone: "bull" },
  { key: "very_bullish", label: "Very bullish", move: 2, tone: "bull" },
];

/** Mirrors backend optimizer.OUTLOOK_STRATEGIES, so the family checkboxes
 * show what the view will search before the request goes out. */
const OUTLOOK_STRATEGIES: Record<Outlook, Strategy[]> = {
  very_bearish: ["long_put", "bear_put"],
  bearish: ["long_put", "bear_put", "bear_call"],
  neutral: ["iron_condor", "iron_butterfly", "call_butterfly", "put_butterfly", "calendar"],
  directional: ["long_straddle", "long_strangle"],
  bullish: ["long_call", "bull_call", "bull_put"],
  very_bullish: ["long_call", "bull_call"],
};

/** Diagonals are not enumerated (see backend optimizer.py). */
const NOT_OFFERED = new Set<Strategy>(["diagonal"]);

function OutlookIcon({ tone, move }: { tone: string; move: number }) {
  // Six arrows, drawn once: steep down, down, flat, split, up, steep up.
  const d =
    tone === "flat"
      ? "M4 12 H20 M15 7 L20 12 L15 17"
      : tone === "both"
        ? "M4 12 H11 M11 12 L18 6 M11 12 L18 18 M15 6 H18 V9 M15 18 H18 V15"
        : tone === "bear"
          ? Math.abs(move) > 1
            ? "M6 5 L18 19 M18 11 V19 H10"
            : "M4 8 L11 14 L14 11 L20 17 M20 11 V17 H14"
          : Math.abs(move) > 1
            ? "M6 19 L18 5 M18 13 V5 H10"
            : "M4 16 L11 10 L14 13 L20 7 M20 13 V7 H14";
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** The at-the-money implied volatility of the chain on screen: the mean of
 * call and put IV at the strike nearest the spot. */
function atmIv(chain: ChainResponse | null): number | null {
  if (!chain || chain.rows.length === 0) return null;
  let best = chain.rows[0];
  for (const r of chain.rows) if (Math.abs(r.strike - chain.spot) < Math.abs(best.strike - chain.spot)) best = r;
  const ivs = [best.call?.iv, best.put?.iv].filter((v): v is number => v != null && v > 0);
  return ivs.length ? ivs.reduce((a, b) => a + b, 0) / ivs.length : null;
}

/** "Buy 486C · Sell 496C" from the backend's "+486C −496C". */
function legsSentence(label: string): string {
  return label
    .split(" ")
    .map((part) => {
      const sign = part[0];
      const rest = part.slice(1);
      return `${sign === "+" ? "Buy" : "Sell"} ${rest}`;
    })
    .join(" · ");
}

/** A metric coloured against the best of the list: the best is green, the
 * rest fade toward amber and then grey -- OptionStrat's convention. */
function tier(value: number, best: number): "top" | "mid" | "low" {
  if (best <= 0) return "low";
  const ratio = value / best;
  return ratio >= 0.9 ? "top" : ratio >= 0.5 ? "mid" : "low";
}

/** The card's payoff: P/L at expiry over the price grid, green above zero
 * and red below, the breakevens dotted, the target amber, the spot blue. */
function MiniPayoff({ payoff, targets }: { payoff: Payoff; targets: number[] }) {
  const W = 260;
  const H = 110;
  const PAD = { l: 34, r: 8, t: 8, b: 16 };
  const xs = payoff.prices;
  const ys = payoff.at_expiry;
  const xMin = xs[0];
  const xMax = xs[xs.length - 1];
  const yMin = Math.min(0, ...ys);
  const yMaxRaw = Math.max(0, ...ys);
  const yMax = yMaxRaw === yMin ? yMin + 1 : yMaxRaw;
  const x = (p: number) => PAD.l + ((p - xMin) / (xMax - xMin)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((yMax - v) / (yMax - yMin)) * (H - PAD.t - PAD.b);
  const path = ys.map((v, i) => `${i === 0 ? "M" : "L"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const zero = y(0);
  const area = (above: boolean) => {
    const clipped = ys.map((v) => (above ? Math.max(v, 0) : Math.min(v, 0)));
    const p = clipped.map((v, i) => `${i === 0 ? "M" : "L"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    return `${p} L${x(xMax).toFixed(1)},${zero.toFixed(1)} L${x(xMin).toFixed(1)},${zero.toFixed(1)} Z`;
  };
  const ticks = [xMin, (xMin + xMax) / 2, xMax];
  return (
    <svg className="opt-mini" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="P/L at expiry">
      <defs>
        <linearGradient id="opt-up" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--delta-up)" stopOpacity="0.55" />
          <stop offset="1" stopColor="var(--delta-up)" stopOpacity="0.05" />
        </linearGradient>
        <linearGradient id="opt-down" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="var(--delta-down)" stopOpacity="0.55" />
          <stop offset="1" stopColor="var(--delta-down)" stopOpacity="0.05" />
        </linearGradient>
      </defs>
      <path d={area(true)} fill="url(#opt-up)" />
      <path d={area(false)} fill="url(#opt-down)" />
      <line className="opt-mini-zero" x1={PAD.l} x2={W - PAD.r} y1={zero} y2={zero} />
      <text className="opt-mini-tick" x={PAD.l - 3} y={y(yMax) + 4} textAnchor="end">
        {formatNum(yMax, 0)}
      </text>
      <text className="opt-mini-tick" x={PAD.l - 3} y={zero + 4} textAnchor="end">
        0
      </text>
      {yMin < 0 && (
        <text className="opt-mini-tick" x={PAD.l - 3} y={y(yMin) + 4} textAnchor="end">
          {formatNum(yMin, 0)}
        </text>
      )}
      {ticks.map((p) => (
        <text key={p} className="opt-mini-tick" x={x(p)} y={H - 4} textAnchor="middle">
          {p.toFixed(p < 10 ? 2 : 0)}
        </text>
      ))}
      <line className="opt-mini-spot" x1={x(payoff.spot)} x2={x(payoff.spot)} y1={PAD.t} y2={H - PAD.b} />
      {targets
        .filter((t) => t >= xMin && t <= xMax)
        .map((t) => (
          <line key={t} className="opt-mini-target" x1={x(t)} x2={x(t)} y1={PAD.t} y2={H - PAD.b} />
        ))}
      {payoff.breakevens
        .filter((b) => b >= xMin && b <= xMax)
        .map((b) => (
          <g key={b}>
            <line className="opt-mini-be" x1={x(b)} x2={x(b)} y1={PAD.t} y2={H - PAD.b} />
            <text className="opt-mini-be-label" x={x(b)} y={PAD.t + 8} textAnchor="middle">
              {b.toFixed(2)}
            </text>
          </g>
        ))}
      <path className="opt-mini-line" d={path} />
    </svg>
  );
}

function ResultCard({
  r,
  bestRor,
  bestChance,
  targets,
  onLoad,
}: {
  r: OptimizerResult;
  bestRor: number;
  bestChance: number;
  targets: number[];
  onLoad: (s: LoadableStructure) => boolean;
}) {
  const [failed, setFailed] = useState(false);
  const rorPct = r.return_on_risk * 100;
  return (
    <li className="opt-card">
      <div className="opt-card-title">{r.strategy_label}</div>
      <div className="opt-card-legs">
        {legsSentence(r.legs_label)} · {weekdayOf(r.expiry)} {formatExpiry(r.expiry)}
      </div>
      <div className="opt-card-stats">
        <span
          className={`opt-stat ror ${tier(r.return_on_risk, bestRor)}`}
          title="P/L at the worst point of the target divided by what the account puts up -- the debit paid, or a credit structure's collateral. Not a probability."
        >
          <strong>{rorPct.toFixed(0)}%</strong> Return on risk
        </span>
        <span
          className={`opt-stat chance ${r.chance != null && bestChance > 0 ? tier(r.chance, bestChance) : "low"}`}
          title="Share of the option market's own implied distribution (at-the-money IV, lognormal, no drift) under which the position is profitable on the horizon date. A model number, not a forecast."
        >
          <strong>{r.chance == null ? "—" : `${(r.chance * 100).toFixed(0)}%`}</strong> Chance
        </span>
        <span className="opt-stat plain" title="P/L if the underlying is at the target on the horizon date, each leg's IV unchanged (with a range: its worst point).">
          <strong className={r.pnl_min >= 0 ? "delta-up" : "delta-down"}>{formatMoney(r.pnl_min)}</strong> Profit
        </span>
        <span className="opt-stat plain" title="What the account puts up: the debit paid, or the collateral of a credit structure.">
          <strong>{formatMoney(r.risk)}</strong> Risk
        </span>
      </div>
      {r.spread.payoff ? <MiniPayoff payoff={r.spread.payoff} targets={targets} /> : null}
      <div className="opt-card-foot">
        <span className="order-hint">
          {r.direction === "debit" ? "Pay" : "Receive"} {formatMoney(Math.abs(r.net_price) * 100 * r.spread.qty)} · max{" "}
          {r.max_profit == null ? "unlimited" : formatMoney(r.max_profit)} / {r.max_loss == null ? "—" : formatMoney(r.max_loss)}
          {r.breakevens.length ? ` · BE ${r.breakevens.map((b) => formatPrice(b)).join(" / ")}` : ""}
        </span>
        <button type="button" className="generate-button opt-load" onClick={() => setFailed(!onLoad({ strategy: r.strategy, ticket: r.ticket }))}>
          Load into ticket
        </button>
      </div>
      {r.spread.warnings.map((w) => (
        <p key={w} className="idea-warning">
          {w}
        </p>
      ))}
      {failed && <p className="order-rejection">Could not load this structure into the ticket.</p>}
    </li>
  );
}

function skippedLine(skipped: { total: number; scored: number; reasons: Record<string, number> }): string {
  const names: Record<string, string> = {
    over_budget: "over budget",
    under_min_risk: "under $5 of risk",
    over_max_loss: "over max loss",
    non_positive_return: "lose at the target",
    no_market: "no market",
    no_iv: "no IV",
    wrong_way_market: "quoted the wrong way",
    risk_shape: "mispriced shape",
    candidate_cap: "beyond the candidate cap",
    strategy_cap: "duplicates of a better one",
  };
  const parts = Object.entries(skipped.reasons)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${n} ${names[k] ?? k}`);
  return `${skipped.total} candidates · ${skipped.scored} priced${parts.length ? " · " + parts.join(" · ") : ""}`;
}

function labelFor(s: Strategy): string {
  const short: Partial<Record<Strategy, string>> = {
    long_call: "Long call",
    long_put: "Long put",
    long_straddle: "Straddle",
    long_strangle: "Strangle",
    bull_call: "Bull call",
    bear_put: "Bear put",
    bull_put: "Bull put",
    bear_call: "Bear call",
    iron_condor: "Iron condor",
    iron_butterfly: "Iron fly",
    call_butterfly: "Call fly",
    put_butterfly: "Put fly",
    calendar: "Calendar",
    covered_call: "Covered call",
    cash_secured_put: "Cash-sec. put",
  };
  return short[s] ?? s;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Expiries as OptionStrat lays them out: a month band with its days. */
function ExpiryChips({ expiries, value, onChange }: { expiries: ExpiryInfo[]; value: string; onChange: (e: string) => void }) {
  const usable = expiries.filter((e) => e.dte >= 1);
  const groups: { month: string; items: ExpiryInfo[] }[] = [];
  for (const e of usable) {
    const key = `${MONTHS[Number(e.expiry.slice(5, 7)) - 1]} ${e.expiry.slice(0, 4)}`;
    const last = groups[groups.length - 1];
    if (last && last.month === key) last.items.push(e);
    else groups.push({ month: key, items: [e] });
  }
  return (
    <div className="opt-expiries" role="group" aria-label="Horizon expiry">
      {groups.map((g) => (
        <div key={g.month} className="opt-expiry-month">
          <div className="opt-expiry-month-label">{g.month.slice(0, 3)}</div>
          <div className="opt-expiry-days">
            {g.items.map((e) => (
              <button
                key={e.expiry}
                type="button"
                className="opt-expiry-day"
                aria-pressed={e.expiry === value}
                title={`${weekdayOf(e.expiry)} ${formatExpiry(e.expiry)} · ${e.dte}d`}
                onClick={() => onChange(e.expiry)}
              >
                {Number(e.expiry.slice(8, 10))}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * OptionStrat's optimizer, on this app's own pipeline: pick a view (or type a
 * target), an expiry, a budget and where on the return-vs-chance line you
 * stand, and the backend enumerates structures from the listed chain, prices
 * them through the ticket's path and ranks them (see backend
 * app/options/optimize.py). Return on risk says what a shape pays if the
 * target is reached; chance is the implied distribution's own odds of any
 * profit. Neither is a recommendation.
 */
export function OptimizerTab({ symbol, chain, expiries, optimizer, onLoad }: OptimizerTabProps) {
  const { result, request, loading, error } = optimizer;
  const remembered = request && request.underlying === symbol ? request : null;
  const spot = chain?.spot ?? null;

  const [outlook, setOutlook] = useState<Outlook | null>((remembered?.outlook as Outlook | undefined) ?? null);
  const [target, setTarget] = useState<string>(remembered?.target_low != null ? String(remembered.target_low) : "");
  const [directionalMove, setDirectionalMove] = useState<number | null>(
    remembered?.target_points && remembered.target_points.length === 2 ? (remembered.target_points[1] - remembered.target_points[0]) / 2 : null,
  );
  const [horizonExpiry, setHorizonExpiry] = useState<string>(remembered?.horizon_expiry ?? "");
  const [budget, setBudget] = useState<string>(remembered?.budget != null ? String(remembered.budget) : "1000");
  const [preference, setPreference] = useState<number>(remembered?.preference ?? 0.5);
  const [more, setMore] = useState(false);
  const [maxLoss, setMaxLoss] = useState<string>(remembered?.max_loss != null ? String(remembered.max_loss) : "");
  const [families, setFamilies] = useState<Set<Strategy>>(
    () =>
      new Set<Strategy>(
        remembered?.strategies ??
          STRATEGY_GROUPS.flatMap((g) => g.strategies).filter((s) => !NOT_OFFERED.has(s) && s !== "covered_call" && s !== "cash_secured_put"),
      ),
  );

  // A fresh symbol: the target starts at its spot, the horizon at the first
  // expiry with a day left (a contract expiring today has no IV to price a
  // horizon on).
  useEffect(() => {
    if (remembered) return;
    if (spot != null && target === "") setTarget(spot.toFixed(2));
    if (horizonExpiry === "") {
      const first = expiries.find((e) => e.dte >= 1);
      if (first) setHorizonExpiry(first.expiry);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, spot, expiries.length]);

  // The one-sigma implied move to the chosen horizon, from the chain on
  // screen: spot x ATM IV x sqrt(T). What the outlook buttons work in.
  const impliedMove = useMemo(() => {
    const iv = atmIv(chain);
    const dte = expiries.find((e) => e.expiry === horizonExpiry)?.dte ?? null;
    if (!spot || !iv || !dte || dte <= 0) return null;
    return spot * iv * Math.sqrt(dte / 365);
  }, [chain, expiries, horizonExpiry, spot]);

  const pickOutlook = (key: Outlook) => {
    setOutlook(key);
    setFamilies(new Set(OUTLOOK_STRATEGIES[key]));
    if (spot == null) return;
    const view = OUTLOOKS.find((o) => o.key === key)!;
    const move = impliedMove ?? spot * 0.02;
    if (view.tone === "both") {
      setDirectionalMove(move);
      setTarget(spot.toFixed(2));
    } else {
      setDirectionalMove(null);
      setTarget((spot + view.move * move).toFixed(2));
    }
  };

  const numeric = (v: string): number | null => {
    const n = Number(v.replace(",", "."));
    return v.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
  };
  const targetValue = numeric(target);
  const targetPct = targetValue != null && spot ? ((targetValue / spot - 1) * 100).toFixed(1) : null;
  const canRun = !!symbol && targetValue != null && horizonExpiry !== "" && families.size > 0;

  const run = () => {
    if (!symbol || !canRun || targetValue == null) return;
    const body: OptimizeRequest = {
      underlying: symbol,
      horizon_expiry: horizonExpiry,
      budget: numeric(budget),
      max_loss: numeric(maxLoss),
      strategies: [...families],
      outlook: outlook,
      preference,
      top_n: 9,
    };
    if (directionalMove != null && spot != null) {
      body.target_points = [Math.round((spot - directionalMove) * 100) / 100, Math.round((spot + directionalMove) * 100) / 100];
    } else {
      body.target_low = targetValue;
    }
    optimizer.run(body);
  };

  const toggle = (s: Strategy) =>
    setFamilies((cur) => {
      const next = new Set(cur);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  if (!symbol) {
    return <div className="widget-empty">Select a symbol to optimize a structure for a price target.</div>;
  }
  const shown = result && result.underlying === symbol ? result : null;
  const bestRor = shown ? Math.max(0, ...shown.results.map((r) => r.return_on_risk)) : 0;
  const bestChance = shown ? Math.max(0, ...shown.results.map((r) => r.chance ?? 0)) : 0;
  const shownTargets = shown ? shown.target.points : [];

  return (
    <div className="idea-tab opt-tab">
      <div className="opt-outlooks" role="group" aria-label="Outlook">
        {OUTLOOKS.map((o) => (
          <button
            key={o.key}
            type="button"
            className={`opt-outlook ${o.tone}`}
            aria-pressed={outlook === o.key}
            onClick={() => pickOutlook(o.key)}
            title={
              o.tone === "flat"
                ? "Target at the spot; condors, flies, calendars"
                : o.tone === "both"
                  ? "A move either way: target one implied move above and below; straddles and strangles"
                  : `Target ${Math.abs(o.move)} implied move${Math.abs(o.move) > 1 ? "s" : ""} ${o.move < 0 ? "below" : "above"} the spot`
            }
          >
            <span className="opt-outlook-icon">
              <OutlookIcon tone={o.tone} move={o.move} />
            </span>
            <span className="opt-outlook-label">{o.label}</span>
          </button>
        ))}
      </div>

      <div className="opt-inputs">
        <label>
          Target price $
          <input
            type="number"
            step={0.01}
            min={0.01}
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              setDirectionalMove(null);
            }}
          />
          {directionalMove != null && spot != null ? (
            <span className="order-hint">
              ±{directionalMove.toFixed(2)} · {formatPrice(spot - directionalMove)} / {formatPrice(spot + directionalMove)}
            </span>
          ) : targetPct != null ? (
            <span className={`order-hint ${Number(targetPct) >= 0 ? "delta-up" : "delta-down"}`}>
              ({Number(targetPct) >= 0 ? "+" : ""}
              {targetPct}%)
            </span>
          ) : null}
        </label>
        <label title="The most the account puts up per position: the debit paid, or a credit structure's collateral.">
          Budget $
          <input type="number" step={50} min={1} value={budget} placeholder="any" onChange={(e) => setBudget(e.target.value)} />
        </label>
        {impliedMove != null && (
          <span className="order-hint" title="One standard deviation of the move the option market prices to this expiry: spot x ATM IV x sqrt(time). The outlook buttons set the target in these.">
            implied move ±{impliedMove.toFixed(2)} to {formatExpiry(horizonExpiry)}
          </span>
        )}
      </div>

      <ExpiryChips expiries={expiries} value={horizonExpiry} onChange={setHorizonExpiry} />

      <div className="opt-preference">
        <span className="opt-pref-label">← Max Return</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={preference}
          onChange={(e) => setPreference(Number(e.target.value))}
          title="Where the ranking stands between the highest return on risk and the highest chance of profit."
        />
        <span className="opt-pref-label">Max Chance →</span>
        <button type="button" className="generate-button opt-run" disabled={!canRun || loading} onClick={run}>
          {loading ? "Pricing structures…" : "Find structures"}
        </button>
        <button type="button" className="row-action" onClick={() => setMore((m) => !m)} aria-expanded={more}>
          {more ? "Fewer options" : "More options"}
        </button>
      </div>

      {more && (
        <div className="opt-more">
          <label title="The largest defined loss to accept; shapes with an unbounded loss never pass it.">
            Max loss $
            <input type="number" step={10} min={1} value={maxLoss} placeholder="any" onChange={(e) => setMaxLoss(e.target.value)} />
          </label>
          <div className="optimizer-families">
            {STRATEGY_GROUPS.map((group) => (
              <span key={group.label} className="optimizer-family">
                <span className="order-hint">{group.label}</span>
                {group.strategies
                  .filter((s) => !NOT_OFFERED.has(s))
                  .map((s) => (
                    <label key={s} className="optimizer-check">
                      <input type="checkbox" checked={families.has(s)} onChange={() => toggle(s)} />
                      {labelFor(s)}
                    </label>
                  ))}
              </span>
            ))}
          </div>
        </div>
      )}

      {error && <p className="order-rejection">{error}</p>}
      {loading && (
        <p className="widget-empty">Loading the chain across the horizon's expiries, pricing every candidate, previewing the finalists…</p>
      )}

      {!loading && shown && (
        <>
          <p className="idea-context">
            {shown.target.points.length === 2 && shown.outlook === "directional"
              ? `Target ${formatPrice(shown.target.points[0])} or ${formatPrice(shown.target.points[1])}`
              : shown.target.high > shown.target.low
                ? `Target ${formatPrice(shown.target.low)} – ${formatPrice(shown.target.high)}`
                : `Target ${formatPrice(shown.target.low)}`}{" "}
            on {weekdayOf(shown.horizon.date)} {formatExpiry(shown.horizon.date)} · spot {formatPrice(shown.spot)}
            {shown.implied_move != null ? ` · implied move ±${shown.implied_move.toFixed(2)}` : ""}
            {shown.atm_iv != null ? ` · ATM IV ${(shown.atm_iv * 100).toFixed(1)}%` : ""} · expiries{" "}
            {shown.horizon.expiries_considered.map((e) => formatExpiry(e)).join(", ")}
          </p>
          {shown.warnings.map((w) => (
            <p key={w} className="idea-warning">
              {w}
            </p>
          ))}
          {shown.results.length === 0 && (
            <p className="widget-empty">
              No structure within the listed strikes pays off at this target under these limits. The line below says
              where the candidates went.
            </p>
          )}
          <ul className="opt-grid">
            {shown.results.map((r) => (
              <ResultCard
                key={`${r.strategy}-${r.expiry}-${r.legs_label}`}
                r={r}
                bestRor={bestRor}
                bestChance={bestChance}
                targets={shownTargets}
                onLoad={onLoad}
              />
            ))}
          </ul>
          {shown.rejected.length > 0 && (
            <ul className="idea-rejected">
              {shown.rejected.map((rej, i) => (
                <li key={`${rej.strategy}-${rej.legs_label}-${i}`}>
                  <strong>{rej.strategy_label}</strong> {rej.legs_label} ({rej.expiry}) — {rej.rejected_because}
                </li>
              ))}
            </ul>
          )}
          <p className="optimizer-skipped">{skippedLine(shown.skipped)}</p>
          <p className="idea-disclaimer">{shown.disclaimer}</p>
        </>
      )}
    </div>
  );
}
