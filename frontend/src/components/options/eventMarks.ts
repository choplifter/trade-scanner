import type { EarningsEvents, ExpiryInfo, MacroEventOut, OptionEventsResponse } from "../../types/options";
import { NATENBERG, withBook } from "./bookRefs";
import { formatExpiry, weekdayOf } from "../../utils/occ";

/** What an expiry is the first to be held through: the earnings report,
 * and the macro releases, that fall after the previous expiry and on or
 * before this one. Later expiries hold the same events too, but the mark
 * sits where the event enters the strip -- a strip dotted from there to
 * its end would say nothing. */
export interface ExpiryMarks {
  earnings: boolean;
  macro: MacroEventOut[];
}

export function eventMarks(expiries: ExpiryInfo[], events: OptionEventsResponse | null): Map<string, ExpiryMarks> {
  const marks = new Map<string, ExpiryMarks>();
  if (!events || expiries.length === 0) return marks;
  const sorted = [...expiries].map((e) => e.expiry).sort();
  const firstOnOrAfter = (day: string) => sorted.find((e) => e >= day) ?? null;
  const mark = (expiry: string) => {
    let m = marks.get(expiry);
    if (!m) marks.set(expiry, (m = { earnings: false, macro: [] }));
    return m;
  };
  const report = events.earnings?.report_date;
  if (report) {
    const e = firstOnOrAfter(report);
    if (e) mark(e).earnings = true;
  }
  for (const ev of events.macro) {
    const e = firstOnOrAfter(ev.date);
    if (e) mark(e).macro.push(ev);
  }
  return marks;
}

/** The releases up to the last expiry on the strip: the calendar looks
 * seventy days ahead, the strip sixty, and a CPI after the last listed
 * expiry is nothing any of these contracts is held through. */
export function macroInWindow(macro: MacroEventOut[], expiries: ExpiryInfo[]): MacroEventOut[] {
  if (expiries.length === 0) return [];
  const last = expiries.reduce((a, e) => (e.expiry > a ? e.expiry : a), expiries[0].expiry);
  return macro.filter((m) => m.date <= last);
}

/** Whether a contract expiring on `expiry` is held through the report. */
export function heldThroughEarnings(expiry: string, earnings: EarningsEvents | null | undefined): boolean {
  return !!earnings?.report_date && earnings.report_date <= expiry;
}

/** "Earnings Wed 28 Oct (54d) · moved ±7.0 % median over 8 reports, max 12.3 %" */
export function earningsSentence(earnings: EarningsEvents): string {
  const parts: string[] = [];
  if (earnings.report_date) {
    parts.push(`Earnings ${weekdayOf(earnings.report_date)} ${formatExpiry(earnings.report_date)} (${earnings.days_until}d)`);
  } else {
    parts.push("No upcoming earnings date known");
  }
  if (earnings.samples > 0 && earnings.median_abs_pct != null) {
    parts.push(
      `moved ±${earnings.median_abs_pct.toFixed(1)} % median over ${earnings.samples} report${earnings.samples === 1 ? "" : "s"}` +
        (earnings.max_abs_pct != null ? `, max ${earnings.max_abs_pct.toFixed(1)} %` : ""),
    );
  } else if (earnings.history_note) {
    parts.push(`past moves: ${earnings.history_note}`);
  }
  return parts.join(" · ");
}

export function macroSentence(macro: MacroEventOut[]): string {
  return macro.map((m) => `${m.label} ${weekdayOf(m.date)} ${formatExpiry(m.date)}`).join(" · ");
}

/** The IV-rank light: rich above 60, cheap below 30 -- the conventional
 * bands premium sellers and buyers use -- and "mid" between. Null without
 * a rank. */
export type IvTone = "rich" | "mid" | "cheap";

export function ivTone(percent: number | null | undefined): IvTone | null {
  if (percent == null) return null;
  return percent >= 60 ? "rich" : percent <= 30 ? "cheap" : "mid";
}

/** Good or bad for the side being traded: rich premium is good to sell
 * and bad to buy, cheap the other way round. The one rule the ticket, the
 * open spreads and the Screener colour volatility by, so a green means the
 * same thing everywhere. */
export type SideTone = "good" | "bad" | "neutral";

export function sideTone(tone: IvTone | null, selling: boolean): SideTone {
  if (tone === "rich") return selling ? "good" : "bad";
  if (tone === "cheap") return selling ? "bad" : "good";
  return "neutral";
}

/** IV over realised as rich / mid / cheap, on the same bands as the rank's. */
export function ratioTone(ratio: number | null | undefined): IvTone | null {
  if (ratio == null) return null;
  return ratio >= RICH_IV_RATIO ? "rich" : ratio <= CHEAP_IV_RATIO ? "cheap" : "mid";
}

/** The CSS class a side tone is painted with. */
export function toneClass(tone: SideTone): string | undefined {
  return tone === "good" ? "delta-up" : tone === "bad" ? "delta-down" : undefined;
}

/** The chain's at-the-money IV beside the VIX -- both are an annualised
 * expectation of a 30-day move, so they are on one scale, and the gap is
 * what says whether this symbol is priced above or below the market's own
 * weather. Only worth showing for a broad index tracker: next to a single
 * stock's IV the VIX is a different thing being compared (see the title
 * the widget puts on it). */
export function vixSentence(atmIv: number | null | undefined, vix: number | null | undefined): string | null {
  if (vix == null) return null;
  const level = `VIX ${vix.toFixed(1)}`;
  if (atmIv == null) return level;
  const chain = atmIv * 100;
  const gap = chain - vix;
  if (Math.abs(gap) < 0.5) return `${level} · this chain is level with it`;
  return `${level} · this chain ${gap > 0 ? "above" : "below"} it by ${Math.abs(gap).toFixed(1)} points`;
}

/** The same bands the Screener judges IV against realised by
 * (RICH_IV_RATIO / CHEAP_IV_RATIO in app/options/screener.py). Implied
 * normally sits a little above realised, so 1.0 is not the middle. */
export const RICH_IV_RATIO = 1.2;
export const CHEAP_IV_RATIO = 0.95;

/** Is the IV high right now, without any history: the chain's ATM IV over
 * the stock's 20-session realised vol. Null without both. */
export function ivPremiumTone(ratio: number | null | undefined): IvTone | null {
  if (ratio == null) return null;
  return ratio >= RICH_IV_RATIO ? "rich" : ratio <= CHEAP_IV_RATIO ? "cheap" : "mid";
}

export function ivPremiumSentence(iv: OptionEventsResponse["iv"]): string | null {
  const ratio = iv.iv_over_realized;
  // Judged on the reference expiry when the chain on screen was too near.
  const judged = iv.reference?.atm_iv ?? iv.atm_iv;
  if (ratio == null || judged == null || iv.realized_vol_20d == null) return null;
  const tone = ivPremiumTone(ratio);
  const gloss = tone === "rich" ? "premium rich" : tone === "cheap" ? "premium cheap" : "premium fair";
  const where = iv.reference ? ` on ${formatExpiry(iv.reference.expiry)} (${iv.reference.dte} d)` : "";
  return `IV ${ratio.toFixed(2)}× realised · ${gloss} (${(judged * 100).toFixed(0)} %${where} vs ${(iv.realized_vol_20d * 100).toFixed(0)} % over 20 sessions)`;
}

export const IV_PREMIUM_TITLE = withBook(
  "At-the-money IV over the stock's realised volatility of the last 20 sessions. Read on the chain shown when it is 20-90 days out; nearer or further, on the 30-60 day expiry instead, because a chain about to expire prices the next few hours rather than the stock. Above 1.20 the market charges noticeably more than the stock has been moving (premium rich, short-vol shapes collect more); under 0.95 less (premium cheap, debit shapes cost less). Implied normally sits a little above realised. After a quiet stretch with an event ahead -- earnings marked in the strip -- a high ratio may be fair rather than rich.",
  NATENBERG.historicalVol,
  NATENBERG.ivAsPredictor,
);

/** Where the IV sits in the stock's volatility cone, said plainly: how
 * often the stock has really moved as much as the option prices. Not a
 * verdict -- implied normally sits above realised, so the upper half of the
 * cone is ordinary. Null without a cone. */
export function coneSentence(iv: OptionEventsResponse["iv"]): string | null {
  const c = iv.cone;
  if (!c) return null;
  const pct = (v: number) => `${(v * 100).toFixed(0)} %`;
  return `IV above ${c.percentile.toFixed(0)} % of ${c.window}-session spells this year (realised ${pct(c.low)}–${pct(c.high)}, median ${pct(c.median)})`;
}

export const CONE_TITLE = withBook(
  "The volatility cone: every spell as long as the option has left to run, over the past year, and how much the stock really moved in each. The figure is the share of those spells that realised less than today's implied volatility -- how often the stock has actually moved as much as the option now prices. Needs only daily closes, so every symbol has one. Not a verdict on its own: implied normally sits above realised (the volatility risk premium), so a reading in the upper half is ordinary; near 100 % the option prices a move the stock has rarely made, near 0 % one it routinely beats.",
  NATENBERG.historicalVol,
);

export function ivRankSentence(iv: OptionEventsResponse["iv"]): string {
  if (iv.rank) {
    const tone = ivTone(iv.rank.percent);
    const gloss = tone === "rich" ? "premium rich" : tone === "cheap" ? "premium cheap" : "premium mid-range";
    return `IV rank ${iv.rank.percent.toFixed(0)} % · ${gloss} (${(iv.rank.low * 100).toFixed(0)}–${(iv.rank.high * 100).toFixed(0)} % over ${iv.rank.samples} sessions)`;
  }
  if (iv.atm_iv == null) return "IV rank: no ATM IV on this chain";
  return `IV rank: not yet (${iv.samples} session${iv.samples === 1 ? "" : "s"} recorded, 20 needed)`;
}
