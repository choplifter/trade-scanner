import type { OptionEventsResponse, ResolvedSpread } from "../../types/options";
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
