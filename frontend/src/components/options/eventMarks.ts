import type { EarningsEvents, ExpiryInfo, MacroEventOut, OptionEventsResponse } from "../../types/options";
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

export function ivRankSentence(iv: OptionEventsResponse["iv"]): string {
  if (iv.rank) {
    const tone = ivTone(iv.rank.percent);
    const gloss = tone === "rich" ? "premium rich" : tone === "cheap" ? "premium cheap" : "premium mid-range";
    return `IV rank ${iv.rank.percent.toFixed(0)} % · ${gloss} (${(iv.rank.low * 100).toFixed(0)}–${(iv.rank.high * 100).toFixed(0)} % over ${iv.rank.samples} sessions)`;
  }
  if (iv.atm_iv == null) return "IV rank: no ATM IV on this chain";
  return `IV rank: not yet (${iv.samples} session${iv.samples === 1 ? "" : "s"} recorded, 20 needed)`;
}
