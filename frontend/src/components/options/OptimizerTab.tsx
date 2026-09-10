import { useEffect, useMemo, useRef, useState } from "react";

import { getChain } from "../../api/options";
import type { OptimizerState } from "../../hooks/useOptionsOptimizer";
import {
  STRATEGY_GROUPS,
  type ChainResponse,
  type ExpiryInfo,
  type LoadableStructure,
  type OptimizeRequest,
  type OptimizerOutlook,
  type OptionEventsResponse,
  type Strategy,
} from "../../types/options";
import { atmIv, impliedMove as impliedMoveOf } from "../../utils/atmIv";
import { formatPrice } from "../../utils/format";
import { formatExpiry, weekdayOf } from "../../utils/occ";
import { earningsSentence, eventMarks, heldThroughEarnings, ivRankSentence, ivTone, macroInWindow, macroSentence } from "./eventMarks";
import type { OptimizerIntent } from "./optimizerIntent";
import { ResultCard } from "./ResultCard";

interface OptimizerTabProps {
  symbol: string | null;
  /** The chain on screen: its spot prefills the target, its at-the-money IV
   * sizes the implied move the outlook buttons work in. */
  chain: ChainResponse | null;
  expiries: ExpiryInfo[];
  /** Earnings, macro releases and IV rank for the symbol (useOptionEvents). */
  events: OptionEventsResponse | null;
  optimizer: OptimizerState;
  /** A scanner row asked for this symbol with a view; run it once the chain
   * is in, then report the seq back so it is not run twice. */
  intent: OptimizerIntent | null;
  onIntentHandled: (seq: number) => void;
  /** Applies a result's ticket to the widget's own strategy/expiry/legs.
   * Returns false when it could not be loaded, so the card can say so. */
  onLoad: (structure: LoadableStructure) => boolean;
}

type Outlook = OptimizerOutlook;

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
  neutral: ["iron_condor", "iron_butterfly", "call_butterfly", "put_butterfly", "calendar", "covered_call"],
  directional: ["long_straddle", "long_strangle"],
  bullish: ["long_call", "bull_call", "bull_put", "cash_secured_put"],
  very_bullish: ["long_call", "bull_call"],
};

/** What the IV-rank light offers: the shapes that sell premium when it is
 * rich, the shapes that buy it when it is cheap. */
const CREDIT_STRATEGIES: Strategy[] = ["bull_put", "bear_call", "iron_condor", "iron_butterfly"];
const DEBIT_STRATEGIES: Strategy[] = ["long_call", "long_put", "bull_call", "bear_put", "long_straddle", "long_strangle", "calendar"];

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

/** Expiries as OptionStrat lays them out: a month band with its days. An
 * expiry that is the first to be held through the earnings report or a
 * macro release carries a dot; with "hold through earnings" off, the ones
 * on or after the report are greyed out. */
function ExpiryChips({
  expiries,
  value,
  events,
  allowThroughEarnings,
  onChange,
}: {
  expiries: ExpiryInfo[];
  value: string;
  events: OptionEventsResponse | null;
  allowThroughEarnings: boolean;
  onChange: (e: string) => void;
}) {
  const usable = expiries.filter((e) => e.dte >= 1);
  const marks = eventMarks(usable, events);
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
            {g.items.map((e) => {
              const m = marks.get(e.expiry);
              const through = heldThroughEarnings(e.expiry, events?.earnings);
              const blocked = through && !allowThroughEarnings;
              const notes = [
                `${weekdayOf(e.expiry)} ${formatExpiry(e.expiry)} · ${e.dte}d`,
                through && events?.earnings?.report_date ? `held through earnings on ${formatExpiry(events.earnings.report_date)}` : null,
                ...(m?.macro ?? []).map((ev) => `${ev.label} ${formatExpiry(ev.date)} (${ev.event})`),
              ].filter(Boolean);
              return (
                <button
                  key={e.expiry}
                  type="button"
                  className={`opt-expiry-day${m?.earnings ? " has-earnings" : ""}${m?.macro.length ? " has-macro" : ""}${blocked ? " blocked" : ""}`}
                  aria-pressed={e.expiry === value}
                  disabled={blocked}
                  title={notes.join(" · ")}
                  onClick={() => onChange(e.expiry)}
                >
                  {Number(e.expiry.slice(8, 10))}
                  {m?.earnings && <span className="opt-dot earnings" aria-label="earnings" />}
                  {m?.macro.length ? <span className="opt-dot macro" aria-label={m.macro.map((x) => x.label).join(", ")} /> : null}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

/** One expiry's chain, fetched on demand (the server caches it 15 s); the
 * tab needs the horizon expiry's and the last pre-earnings expiry's ATM IV
 * to price the report itself. Null until in, or when it cannot be had. */
function useChainFor(symbol: string | null, expiry: string | null, enabled: boolean, fallback: ChainResponse | null): ChainResponse | null {
  const [chain, setChain] = useState<ChainResponse | null>(null);
  useEffect(() => {
    if (!enabled || !symbol || !expiry) return;
    if (fallback && fallback.underlying === symbol && fallback.expiry === expiry) return;
    let cancelled = false;
    getChain(symbol, expiry)
      .then((c) => {
        if (!cancelled) setChain(c);
      })
      .catch(() => {
        if (!cancelled) setChain(null);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, expiry, enabled, fallback]);
  if (fallback && symbol && fallback.underlying === symbol && fallback.expiry === expiry) return fallback;
  return chain && chain.underlying === symbol && chain.expiry === expiry ? chain : null;
}

/** The report's own implied move, as a percentage of spot: the horizon
 * expiry's implied variance less the last pre-report expiry's. The
 * ordinary days cancel; what is left is what the market charges for the
 * print. Null when either chain or IV is missing or the excess is not
 * positive. */
function earningsImpliedPct(horizon: ChainResponse | null, horizonDte: number | null, pre: ChainResponse | null, preDte: number | null): number | null {
  const ivH = atmIv(horizon);
  const ivP = atmIv(pre);
  if (ivH == null || ivP == null || horizonDte == null || preDte == null || horizonDte <= preDte) return null;
  const excess = ivH * ivH * (horizonDte / 365) - ivP * ivP * (preDte / 365);
  return excess > 0 ? Math.sqrt(excess) * 100 : null;
}

interface RunParams {
  outlook: Outlook | null;
  target: number | null;
  directionalMove: number | null;
  families: Set<Strategy>;
  horizonExpiry: string;
  budget: number | null;
  maxLoss: number | null;
  preference: number;
  avoidEarnings: boolean;
}

/**
 * OptionStrat's optimizer, on this app's own pipeline: pick a view (or type a
 * target), an expiry, a budget and where on the return-vs-chance line you
 * stand, and the backend enumerates structures from the listed chain, prices
 * them through the ticket's path and ranks them (see backend
 * app/options/optimize.py). Return on risk says what a shape pays if the
 * target is reached; chance is the implied distribution's own odds of any
 * profit. The events line says what the horizon is held through (earnings,
 * FOMC, CPI) and how the stock moved over its past reports; the IV-rank
 * light says whether premium is rich or cheap against its own history.
 * Nothing here is a recommendation.
 */
export function OptimizerTab({ symbol, chain, expiries, events, optimizer, intent, onIntentHandled, onLoad }: OptimizerTabProps) {
  const { result, request, loading, error } = optimizer;
  const remembered = request && request.underlying === symbol ? request : null;
  const spot = chain?.spot ?? null;
  const symbolEvents = events && events.underlying === symbol ? events : null;
  const earnings = symbolEvents?.earnings ?? null;
  const macroShown = useMemo(() => macroInWindow(symbolEvents?.macro ?? [], expiries), [symbolEvents, expiries]);

  const [outlook, setOutlook] = useState<Outlook | null>((remembered?.outlook as Outlook | undefined) ?? null);
  const [target, setTarget] = useState<string>(remembered?.target_low != null ? String(remembered.target_low) : "");
  const [directionalMove, setDirectionalMove] = useState<number | null>(
    remembered?.target_points && remembered.target_points.length === 2 ? (remembered.target_points[1] - remembered.target_points[0]) / 2 : null,
  );
  const [horizonExpiry, setHorizonExpiry] = useState<string>(remembered?.horizon_expiry ?? "");
  const [budget, setBudget] = useState<string>(remembered?.budget != null ? String(remembered.budget) : "1000");
  const [preference, setPreference] = useState<number>(remembered?.preference ?? 0.5);
  const [holdThroughEarnings, setHoldThroughEarnings] = useState<boolean>(!(remembered?.avoid_earnings ?? false));
  const [more, setMore] = useState(false);
  const [maxLoss, setMaxLoss] = useState<string>(remembered?.max_loss != null ? String(remembered.max_loss) : "");
  const [families, setFamilies] = useState<Set<Strategy>>(
    () =>
      new Set<Strategy>(
        remembered?.strategies ??
          STRATEGY_GROUPS.flatMap((g) => g.strategies).filter((s) => !NOT_OFFERED.has(s) && s !== "covered_call" && s !== "cash_secured_put"),
      ),
  );
  const [reason, setReason] = useState<string | null>(null);

  // A new symbol without a remembered run for it starts from a clean form:
  // the old symbol's horizon is not a listed expiry of the new one, and its
  // target is a price on another chart.
  const symbolRef = useRef(symbol);
  useEffect(() => {
    if (symbolRef.current === symbol) return;
    symbolRef.current = symbol;
    // A run remembered for this symbol restores its form; otherwise clean.
    const r = request && request.underlying === symbol ? request : null;
    setOutlook((r?.outlook as Outlook | undefined) ?? null);
    setTarget(r?.target_low != null ? String(r.target_low) : "");
    setDirectionalMove(r?.target_points && r.target_points.length === 2 ? (r.target_points[1] - r.target_points[0]) / 2 : null);
    setHorizonExpiry(r?.horizon_expiry ?? "");
    setHoldThroughEarnings(!(r?.avoid_earnings ?? false));
    if (r?.strategies) setFamilies(new Set(r.strategies));
    setReason(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  // A horizon that is not one of this symbol's listed expiries (a stale one
  // from before the switch, an expiry that has since dropped off) falls back
  // to the first with a day left.
  useEffect(() => {
    if (expiries.length === 0 || horizonExpiry === "") return;
    if (expiries.some((e) => e.expiry === horizonExpiry)) return;
    setHorizonExpiry(expiries.find((e) => e.dte >= 1)?.expiry ?? "");
  }, [expiries, horizonExpiry]);

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

  // With "hold through earnings" off, a horizon on or after the report is
  // moved back to the last expiry before it.
  useEffect(() => {
    if (holdThroughEarnings || !earnings?.report_date || !horizonExpiry) return;
    if (!heldThroughEarnings(horizonExpiry, earnings)) return;
    const before = expiries.filter((e) => e.dte >= 1 && !heldThroughEarnings(e.expiry, earnings));
    if (before.length) setHorizonExpiry(before[before.length - 1].expiry);
  }, [holdThroughEarnings, earnings, horizonExpiry, expiries]);

  // The one-sigma implied move to the chosen horizon, from the chain on
  // screen: spot x ATM IV x sqrt(T). What the outlook buttons work in.
  const chainIv = useMemo(() => atmIv(chain), [chain]);
  const horizonDte = expiries.find((e) => e.expiry === horizonExpiry)?.dte ?? null;
  const impliedMove = useMemo(() => impliedMoveOf(spot, chainIv, horizonDte), [spot, chainIv, horizonDte]);

  // Earnings inside the horizon: the horizon expiry's chain and the last
  // pre-report expiry's, so the report's own implied move can be read off
  // their difference (see earningsImpliedPct).
  const horizonThroughEarnings = heldThroughEarnings(horizonExpiry, earnings);
  const preExpiry = useMemo(() => {
    if (!earnings?.report_date) return null;
    const before = expiries.filter((e) => e.dte >= 1 && e.expiry < earnings.report_date!);
    return before.length ? before[before.length - 1].expiry : null;
  }, [expiries, earnings]);
  const preDte = expiries.find((e) => e.expiry === preExpiry)?.dte ?? null;
  const horizonChain = useChainFor(symbol, horizonExpiry || null, horizonThroughEarnings, chain);
  const preChain = useChainFor(symbol, preExpiry, horizonThroughEarnings, chain);
  const reportImpliedPct = horizonThroughEarnings ? earningsImpliedPct(horizonChain, horizonDte, preChain, preDte) : null;

  /** The target and families a view implies, without touching state -- so
   * the button and a scanner intent share one definition. */
  const viewParams = (key: Outlook): Pick<RunParams, "outlook" | "target" | "directionalMove" | "families"> => {
    const view = OUTLOOKS.find((o) => o.key === key)!;
    const fam = new Set(OUTLOOK_STRATEGIES[key]);
    if (spot == null) return { outlook: key, target: null, directionalMove: null, families: fam };
    const move = impliedMove ?? spot * 0.02;
    if (view.tone === "both") return { outlook: key, target: spot, directionalMove: move, families: fam };
    return { outlook: key, target: spot + view.move * move, directionalMove: null, families: fam };
  };

  const pickOutlook = (key: Outlook) => {
    const p = viewParams(key);
    setOutlook(key);
    setFamilies(p.families);
    setDirectionalMove(p.directionalMove);
    if (p.target != null) setTarget(p.target.toFixed(2));
    setReason(null);
  };

  const numeric = (v: string): number | null => {
    const n = Number(v.replace(",", "."));
    return v.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
  };
  const targetValue = numeric(target);
  const targetPct = targetValue != null && spot ? ((targetValue / spot - 1) * 100).toFixed(1) : null;
  const canRun = !!symbol && targetValue != null && horizonExpiry !== "" && families.size > 0;

  const bodyFor = (p: RunParams): OptimizeRequest | null => {
    if (!symbol || p.target == null || !p.horizonExpiry || p.families.size === 0) return null;
    const body: OptimizeRequest = {
      underlying: symbol,
      horizon_expiry: p.horizonExpiry,
      budget: p.budget,
      max_loss: p.maxLoss,
      strategies: [...p.families],
      outlook: p.outlook,
      preference: p.preference,
      avoid_earnings: p.avoidEarnings,
      top_n: 9,
    };
    if (p.directionalMove != null && spot != null) {
      body.target_points = [Math.round((spot - p.directionalMove) * 100) / 100, Math.round((spot + p.directionalMove) * 100) / 100];
    } else {
      body.target_low = Math.round(p.target * 100) / 100;
    }
    return body;
  };

  const run = () => {
    const body = bodyFor({
      outlook,
      target: targetValue,
      directionalMove,
      families,
      horizonExpiry,
      budget: numeric(budget),
      maxLoss: numeric(maxLoss),
      preference,
      avoidEarnings: !holdThroughEarnings,
    });
    if (body) optimizer.run(body);
  };

  // A scanner row's request: once this symbol's chain (and so its implied
  // move) is on screen, take the view, show it in the form and run it.
  const handledRef = useRef<number>(0);
  useEffect(() => {
    if (!intent || intent.seq === handledRef.current) return;
    if (!symbol || intent.symbol !== symbol || !chain || chain.underlying !== symbol || spot == null) return;
    if (horizonExpiry === "" || !expiries.some((e) => e.expiry === horizonExpiry)) return;
    handledRef.current = intent.seq;
    const p = viewParams(intent.outlook);
    const over = intent.request ?? {};
    // A caller may bring its own families, horizon and target -- the
    // Earnings screen sends the ones it scored, so the form shows the
    // question that was actually asked rather than the outlook's default.
    const families = over.strategies ? new Set(over.strategies) : p.families;
    const horizon = over.horizon_expiry && expiries.some((e) => e.expiry === over.horizon_expiry) ? over.horizon_expiry : horizonExpiry;
    const target = over.target_low != null ? over.target_low : p.target;
    setOutlook(p.outlook);
    setFamilies(families);
    setDirectionalMove(p.directionalMove);
    if (target != null) setTarget(target.toFixed(2));
    setHorizonExpiry(horizon);
    setReason(intent.reason ?? null);
    const body = bodyFor({
      ...p,
      families,
      target,
      horizonExpiry: horizon,
      budget: numeric(budget),
      maxLoss: numeric(maxLoss),
      preference,
      avoidEarnings: !holdThroughEarnings,
    });
    if (body) optimizer.run({ ...body, ...over });
    onIntentHandled(intent.seq);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intent?.seq, symbol, chain?.underlying, spot, horizonExpiry, impliedMove]);

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
  // What produced the answer on screen, for the empty state's explanation:
  // the budget it ran under, and whether it was asked for a structure that
  // puts up shares or cash rather than a spread's width.
  const shownBudget = remembered?.budget ?? null;
  const shownHasIncome = (remembered?.strategies ?? []).some((s) => s === "covered_call" || s === "cash_secured_put");
  const bestRor = shown ? Math.max(0, ...shown.results.map((r) => r.return_on_risk)) : 0;
  const bestChance = shown ? Math.max(0, ...shown.results.map((r) => r.chance ?? 0)) : 0;
  const shownTargets = shown ? shown.target.points : [];

  // The event read for the chosen horizon: what it is held through, and how
  // the market's pricing compares with the stock's own record.
  const macroInHorizon = (symbolEvents?.macro ?? []).filter((m) => m.date <= horizonExpiry);
  const macroLabelsInHorizon = [...new Set(macroInHorizon.map((m) => m.label))];
  const typicalPct = earnings?.median_abs_pct ?? null;
  const ratio = reportImpliedPct != null && typicalPct != null && reportImpliedPct > 0 ? typicalPct / reportImpliedPct : null;
  const priced = reportImpliedPct != null && preExpiry ? `The market prices ±${reportImpliedPct.toFixed(1)} % for the report itself (the ${formatExpiry(horizonExpiry)} expiry's implied move over the ${formatExpiry(preExpiry)} one)` : null;
  const eventSuggestion: { view: Outlook; text: string } | null =
    horizonThroughEarnings && ratio != null && priced && typicalPct != null && earnings
      ? ratio >= 1.15
        ? {
            view: "directional",
            text: `${priced}; the stock moved ±${typicalPct.toFixed(1)} % median over its last ${earnings.samples} reports. It has usually moved more than is priced: buying the move (straddle, strangle) has been the cheaper side.`,
          }
        : ratio <= 0.85
          ? {
              view: "neutral",
              text: `${priced}; the stock moved ±${typicalPct.toFixed(1)} % median over its last ${earnings.samples} reports. It has usually moved less than is priced: selling the move (condor, iron fly) has been the cheaper side.`,
            }
          : {
              view: "neutral",
              text: `${priced}, about what the stock moved (±${typicalPct.toFixed(1)} % median over ${earnings.samples} reports). Neither side has had an edge on size alone.`,
            }
      : null;
  const ivRank = symbolEvents?.iv.rank ?? null;
  const tone = ivTone(ivRank?.percent);

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
        {symbolEvents && (
          <span
            className={`opt-ivrank ${tone ?? "none"}`}
            title={`${ivRankSentence(symbolEvents.iv)}. Where today's at-the-money IV sits between the lowest and highest recorded over the past year of sessions: above 60 % premium is rich against its own history and credit shapes collect more, below 30 % it is cheap and debit shapes cost less. A comparison with the past, not a forecast.`}
          >
            <span className="opt-ivrank-dot" aria-hidden="true" />
            {ivRank ? `IV rank ${ivRank.percent.toFixed(0)} %` : "IV rank n/a"}
            {tone === "rich" && (
              <button type="button" className="row-action" onClick={() => setFamilies(new Set(CREDIT_STRATEGIES))} title="Search the shapes that sell premium: bull put, bear call, iron condor, iron fly">
                credit shapes
              </button>
            )}
            {tone === "cheap" && (
              <button type="button" className="row-action" onClick={() => setFamilies(new Set(DEBIT_STRATEGIES))} title="Search the shapes that buy premium: long call/put, debit spreads, straddle, strangle, calendar">
                debit shapes
              </button>
            )}
          </span>
        )}
      </div>

      <ExpiryChips expiries={expiries} value={horizonExpiry} events={symbolEvents} allowThroughEarnings={holdThroughEarnings} onChange={setHorizonExpiry} />

      {symbolEvents && (earnings || macroShown.length > 0) && (
        <div className="opt-events">
          {earnings && (
            <label className="opt-event earnings" title="Off: every expiry on or after the report is left out, so no structure is held through it.">
              <span className="opt-dot earnings" aria-hidden="true" />
              {earningsSentence(earnings)}
              {earnings.report_date && (
                <>
                  {" · "}
                  <input type="checkbox" checked={holdThroughEarnings} onChange={(e) => setHoldThroughEarnings(e.target.checked)} /> hold through earnings
                </>
              )}
            </label>
          )}
          {macroShown.length > 0 && (
            <span className="opt-event macro" title="Scheduled US releases inside the strip's window: FOMC decision, CPI, payrolls, PCE, GDP. Index options price these days; the expiry chips dot the first expiry held through each.">
              <span className="opt-dot macro" aria-hidden="true" />
              {macroSentence(macroShown)}
            </span>
          )}
        </div>
      )}

      {eventSuggestion && (
        <div className="opt-suggestion">
          <span>
            <strong>Earnings inside this horizon.</strong> {eventSuggestion.text}
            {macroLabelsInHorizon.length ? ` Also inside it: ${macroLabelsInHorizon.join(", ")}.` : ""}
          </span>
          <button type="button" className="row-action" onClick={() => pickOutlook(eventSuggestion.view)}>
            {eventSuggestion.view === "directional" ? "Use Directional" : "Use Neutral"}
          </button>
        </div>
      )}
      {!eventSuggestion && horizonThroughEarnings && earnings?.report_date && (
        <p className="opt-suggestion plain">
          <strong>Earnings inside this horizon</strong> ({formatExpiry(earnings.report_date)}): the IV in every price below includes the
          report and will not survive it.
          {typicalPct != null ? ` The stock moved ±${typicalPct.toFixed(1)} % median over its last ${earnings.samples} reports.` : ""}
          {earnings.history_note ? ` Past moves: ${earnings.history_note}.` : ""}
          {preExpiry ? " What the market prices for the report needs the pre-report expiry's chain, which is not in yet." : " No expiry before the report is listed, so the report's own implied move cannot be separated."}
        </p>
      )}
      {!horizonThroughEarnings && macroInHorizon.length > 0 && (
        <p className="opt-suggestion plain">
          <strong>Inside this horizon:</strong> {macroInHorizon.map((m) => `${m.label} ${formatExpiry(m.date)}`).join(", ")}. A release day
          moves the whole market, so the day's range is larger than a quiet day's implied move; on the index ETFs the 0DTE GEX walls on
          the chart say where price is pinned going into it.
        </p>
      )}

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

      {error && remembered && <p className="order-rejection">{error}</p>}
      {loading && (
        <p className="widget-empty">Loading the chain across the horizon's expiries, pricing every candidate, previewing the finalists…</p>
      )}

      {!loading && shown && (
        <>
          <p className="idea-context">
            {reason ? `From the scanner (${reason}) · ` : ""}
            {shown.target.points.length === 2 && shown.outlook === "directional"
              ? `Target ${formatPrice(shown.target.points[0])} or ${formatPrice(shown.target.points[1])}`
              : shown.target.high > shown.target.low
                ? `Target ${formatPrice(shown.target.low)} – ${formatPrice(shown.target.high)}`
                : `Target ${formatPrice(shown.target.low)}`}{" "}
            on {weekdayOf(shown.horizon.date)} {formatExpiry(shown.horizon.date)} · spot {formatPrice(shown.spot)}
            {shown.implied_move != null ? ` · implied move ±${shown.implied_move.toFixed(2)}` : ""}
            {shown.atm_iv != null ? ` · ATM IV ${(shown.atm_iv * 100).toFixed(1)}%` : ""} · expiries{" "}
            {shown.horizon.expiries_considered.map((e) => formatExpiry(e)).join(", ")}
            {shown.earnings?.avoided ? " · earnings avoided" : shown.earnings?.held_through ? " · held through earnings" : ""}
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
          {shown.results.length === 0 && (shown.skipped.reasons.over_budget ?? 0) === shown.skipped.total && shown.skipped.total > 0 && (
            <p className="idea-warning">
              Every shape costs more than the budget{shownBudget != null ? ` of ${formatPrice(shownBudget)}` : ""}.
              {shownHasIncome
                ? " An income structure puts up the position itself: a covered call the hundred shares, a cash-secured put the strike in cash -- thousands, where a spread puts up the width. Raise the budget, or uncheck those families."
                : " Raise it, or name a target a cheaper shape can still pay at."}
            </p>
          )}
          <p className="optimizer-skipped">{skippedLine(shown.skipped)}</p>
          <p className="idea-disclaimer">{shown.disclaimer}</p>
        </>
      )}
    </div>
  );
}
