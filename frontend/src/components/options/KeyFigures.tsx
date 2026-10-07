import type { HeldOutlook, OptionEventsResponse, ResolvedSpread } from "../../types/options";
import { formatExpiry } from "../../utils/occ";
import { NATENBERG, withBook } from "./bookRefs";
import { CHEAP_IV_RATIO, RICH_IV_RATIO } from "./eventMarks";

type Tone = "good" | "bad" | "neutral";

interface Figure {
  label: string;
  value: string;
  tone: Tone;
  title: string;
}

/** The five figures to read before placing a structure, side by side:
 * is the premium rich (IV/RV), is the trade worth it on average (EV), can
 * the worst case be carried (max loss against equity), is a known jump
 * inside its life (earnings), and what crossing the market costs (quote).
 * Each is coloured on its own rule; none is a verdict on the trade. */
export function keyFigures(
  spread: ResolvedSpread,
  events: OptionEventsResponse | null,
  equity: number | null,
  selling: boolean,
): Figure[] {
  const out: Figure[] = [];

  const ratio = events?.iv.iv_over_realized ?? null;
  out.push({
    label: "IV/RV",
    value: ratio == null ? "—" : `${ratio.toFixed(2)}×`,
    // Rich helps a seller, cheap a buyer.
    tone: ratio == null ? "neutral" : ratio >= RICH_IV_RATIO ? (selling ? "good" : "bad") : ratio <= CHEAP_IV_RATIO ? (selling ? "bad" : "good") : "neutral",
    title: withBook(
      `Implied volatility over the stock's realised volatility of the last 20 sessions. From ${RICH_IV_RATIO.toFixed(2)} the premium is rich -- good for selling it; up to ${CHEAP_IV_RATIO.toFixed(2)} cheap -- good for buying it.`,
      NATENBERG.historicalVol,
      NATENBERG.ivAsPredictor,
    ),
  });

  const ev = spread.expected_value ?? null;
  out.push({
    label: "EV",
    value: ev == null ? "—" : `${ev >= 0 ? "+" : "−"}$${Math.abs(ev).toFixed(0)}`,
    tone: ev == null ? "neutral" : ev > 0 ? "good" : "bad",
    title: withBook(
      "Expected value: the mean result at expiry for this order at your limit, on the at-the-money distribution, less the part of the bid/ask the limit has not paid. Negative: on average the premium does not pay for the risk, however high the chance of profit.",
      NATENBERG.probability,
      NATENBERG.impliedDistributions,
    ),
  });

  const evRv = spread.expected_value_rv ?? null;
  out.push({
    label: "EV (RV)",
    value: evRv == null ? "—" : `${evRv >= 0 ? "+" : "−"}$${Math.abs(evRv).toFixed(0)}`,
    tone: evRv == null ? "neutral" : evRv > 0 ? "good" : "bad",
    title: withBook(
      "The same expected value with the odds taken from how much the stock is expected to move -- its realised volatility, the last 20 sessions blended toward the past year by the time left -- instead of how much the options price in. The EV above is about zero for anything priced at its own IV; this one is where implied running above realised shows up as an edge, or does not. A forecast, not a promise: realised volatility changes.",
      NATENBERG.historicalVol,
      NATENBERG.impliedDistributions,
    ),
  });

  // The expected P/L spread over the days left: what the order earns on
  // average per calendar day if the stock moves as its realised-vol
  // forecast says. Theta -- the P/L per day at a standing price -- beside it
  // in the tooltip: the two part ways exactly when the stock moves.
  const days = Math.max(spread.dte, 1);
  const perDay = evRv == null ? null : evRv / days;
  const theta = spread.greeks?.theta ?? null;
  out.push({
    label: "P/L / day",
    value: perDay == null ? "—" : `${perDay >= 0 ? "+" : "−"}$${Math.abs(perDay).toFixed(2)}`,
    tone: perDay == null ? "neutral" : perDay > 0 ? "good" : "bad",
    title: withBook(
      `Expected P/L per calendar day: EV (RV) over the ${spread.dte} days left. ` +
        (theta != null ? `Theta, the P/L per day if the stock stood still, is ${theta >= 0 ? "+" : "−"}$${Math.abs(theta).toFixed(2)}; the gap between the two is what the expected movement costs (or pays). ` : "") +
        "An average over the life, not a daily schedule: most of a credit spread's P/L arrives late, and a single move can outweigh many days of it.",
      NATENBERG.theta,
      NATENBERG.historicalVol,
    ),
  });

  // The two volatilities the whole trade turns on, as levels: what the
  // options price in (ATM IV) against what the stock is expected to realise.
  const iv = spread.atm_iv ?? null;
  const rv = spread.vol_forecast?.forecast ?? spread.realised_vol ?? null;
  const volRatio = iv != null && rv ? iv / rv : null;
  out.push({
    label: "Vol",
    value: iv == null && rv == null ? "—" : `IV ${iv == null ? "—" : `${(iv * 100).toFixed(1)} %`} · RV ${rv == null ? "—" : `${(rv * 100).toFixed(1)} %`}`,
    tone:
      volRatio == null
        ? "neutral"
        : volRatio >= RICH_IV_RATIO
          ? selling ? "good" : "bad"
          : volRatio <= CHEAP_IV_RATIO
            ? selling ? "bad" : "good"
            : "neutral",
    title: withBook(
      "IV: the at-the-money implied volatility the options price in. RV: the volatility the stock is expected to realise over the position's life -- its last 20 sessions blended toward the past year by the time left" +
        (spread.vol_forecast ? ` (20-session ${(spread.vol_forecast.recent * 100).toFixed(1)} %` + (spread.vol_forecast.long_run != null ? `, year ${(spread.vol_forecast.long_run * 100).toFixed(1)} %)` : ")") : "") +
        ". Selling pays when IV stands well above RV, buying when it stands below.",
      NATENBERG.historicalVol,
      NATENBERG.ivAsPredictor,
    ),
  });

  const loss = spread.max_loss;
  const share = loss != null && equity ? Math.abs(loss) / equity : null;
  out.push({
    label: "Max loss",
    value: loss == null ? "unbounded" : share == null ? `$${Math.abs(loss).toFixed(0)}` : `${(share * 100).toFixed(1)} %`,
    tone: loss == null ? "bad" : share == null ? "neutral" : share <= 0.01 ? "good" : share <= 0.02 ? "neutral" : "bad",
    title: `The most this order can lose${loss != null ? ` ($${Math.abs(loss).toFixed(0)})` : ""} against the account's equity. Up to 1 % green, up to 2 % neutral, above that red -- the daily method's rule is 1 % a trade.`,
  });

  const report = events?.earnings?.report_date ?? null;
  const inside = report != null && report <= spread.expiry;
  out.push({
    label: "Earnings",
    value: report == null ? "none known" : inside ? formatExpiry(report) : `after (${formatExpiry(report)})`,
    tone: inside ? "bad" : "good",
    title: withBook(
      "The next earnings report, red when it falls before this expiry: a jump overnight that no stop can catch.",
      NATENBERG.gaps,
    ),
  });

  const mid = spread.net_mid;
  const natural = spread.net_natural;
  const cross = natural != null && mid > 0 ? Math.abs(natural - mid) / mid : null;
  out.push({
    label: "Quote",
    value: cross == null ? "—" : `${(cross * 100).toFixed(0)} %`,
    tone: cross == null ? "neutral" : cross <= 0.1 ? "good" : cross <= 0.25 ? "neutral" : "bad",
    title: `What crossing the market costs against the mid: natural ${natural?.toFixed(2) ?? "—"} vs mid ${mid.toFixed(2)}. Up to 10 % green, above 25 % red -- there the bid/ask eats a large share of the premium.`,
  });
  return out;
}

/** "$2.0k" from 10k down to 1k, "$27k" above, "$850" below; null: unlimited. */
function compact(v: number | null): string {
  if (v == null) return "∞";
  const a = Math.abs(v);
  if (a >= 10_000) return `$${Math.round(a / 1000)}k`;
  if (a >= 1_000) return `$${(a / 1000).toFixed(1)}k`;
  return `$${a.toFixed(0)}`;
}

/** A held position's two forward figures -- what holding to expiry can
 * bring, as two chances and amounts, and Vol:
 * from today's mark to the (short) expiry, if the stock moves as its
 * realised-vol forecast says -- backend service.held_outlook. Selling: the
 * position was put on for a credit. */
export function heldFigures(outlook: HeldOutlook, theta: number | null, selling: boolean): Figure[] {
  const ev = outlook.expected_value_rv;
  const iv = outlook.iv;
  const rv = outlook.rv_forecast;
  const ratio = iv != null && rv ? iv / rv : null;
  const f = outlook.vol_forecast;
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const up = (v: number | null) => (v == null ? "unlimited" : `$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
  const avg = (v: number | null) => (v == null ? "" : ` (on average $${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })})`);
  return [
    {
      label: "If held",
      value: `${pct(outlook.chance_gain)} +${compact(outlook.max_gain)} · ${pct(outlook.chance_loss)} −${compact(outlook.max_loss)}`,
      tone: ev > 0 ? "good" : ev < 0 ? "bad" : "neutral",
      title: withBook(
        `If held to expiry (${outlook.dte} days): ${pct(outlook.chance_gain)} chance it ends above what it is worth today, adding up to ${up(outlook.max_gain)}${avg(outlook.avg_gain)}; ` +
          `${pct(outlook.chance_loss)} chance it ends below, losing up to ${up(outlook.max_loss)}${avg(outlook.avg_loss)}. ` +
          `Both together average ${ev >= 0 ? "+" : "−"}$${Math.abs(ev).toFixed(0)} (${outlook.pnl_per_day >= 0 ? "+" : "−"}$${Math.abs(outlook.pnl_per_day).toFixed(2)} a day). ` +
          (theta != null ? `Theta, what a day earns if the stock stands still: ${theta >= 0 ? "+" : "−"}$${Math.abs(theta).toFixed(2)}. ` : "") +
          `The odds assume the stock moves as its realised-vol forecast says (${(rv * 100).toFixed(1)} %); what is already won or lost does not count.`,
        NATENBERG.probability,
        NATENBERG.historicalVol,
      ),
    },
    {
      label: "Vol",
      value: `IV ${iv == null ? "—" : `${(iv * 100).toFixed(1)} %`} · RV ${(rv * 100).toFixed(1)} %`,
      tone:
        ratio == null ? "neutral" : ratio >= RICH_IV_RATIO ? (selling ? "good" : "bad") : ratio <= CHEAP_IV_RATIO ? (selling ? "bad" : "good") : "neutral",
      title: withBook(
        "IV: the implied volatility of the position's short legs (all legs when none is short). RV: the volatility the stock is expected to realise over the days left -- its last 20 sessions" +
          ` (${(f.recent * 100).toFixed(1)} %)` +
          (f.long_run != null ? ` blended toward the past year (${(f.long_run * 100).toFixed(1)} %)` : "") +
          ". A short position keeps paying while IV stands well above RV.",
        NATENBERG.historicalVol,
        NATENBERG.ivAsPredictor,
      ),
    },
  ];
}

export function KeyFigures({ figures }: { figures: Figure[] }) {
  return (
    <div className="key-figures" role="group" aria-label="Key figures">
      {figures.map((f) => (
        <span key={f.label} className={`key-figure ${f.tone}`} title={f.title}>
          <span className="key-figure-label">{f.label}</span> {f.value}
        </span>
      ))}
    </div>
  );
}
