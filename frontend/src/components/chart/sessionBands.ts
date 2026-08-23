import type { HistogramData, HistogramSeriesPartialOptions, UTCTimestamp, WhitespaceData } from "lightweight-charts";

/**
 * Session background bands, the way TradingView tints extended hours:
 * premarket and after-hours bars get a translucent wash behind them, the
 * regular session stays on the plain background. The tint answers at a
 * glance which prints happened inside the auction and which on the thin
 * tape around it -- the same distinction VWAP anchoring, the premarket
 * range and the strategy session gates are all built on.
 *
 * Implemented as two Histogram series pinned to a fixed 0..1 overlay scale
 * with zero margins, so a value of 1 is a column spanning the whole pane;
 * bars outside a band's session are whitespace, and a histogram draws
 * nothing there. A column per bar, deliberately: two dead ends came first.
 * A series primitive with drawBackground looked like the purpose-built
 * slot, but its renderer never reached any of the chart's canvases; an
 * Area series then drew, but interpolates its fill straight across
 * whitespace gaps, which painted the premarket wash over the whole prior
 * afternoon and stacked both washes over each other overnight. Columns
 * cannot bridge a gap by construction. Added to the chart before every
 * price-bearing series, so everything else draws over them.
 *
 * Boundaries are the fixed ET clock: 04:00-09:30 premarket, 09:30-16:00
 * regular, 16:00-20:00 after-hours. Half days (early closes) shade
 * 13:00-16:00 as if it were regular session, because the frontend has no
 * exchange calendar -- the same approximation TradingView's fixed session
 * template makes. Classification is by exchange time regardless of the
 * viewer's timezone, so a Berlin viewer's chart shades the same bars as a
 * New York one.
 */

export type SessionKind = "pre" | "regular" | "post";

/** Minutes after midnight ET. The bell and the close, as fixed clock times. */
const REGULAR_OPEN_MINUTES = 9 * 60 + 30;
const REGULAR_CLOSE_MINUTES = 16 * 60;

/** TradingView's convention, at an alpha subtle enough for both themes:
 * amber-ish for the premarket, blue-ish for after-hours. */
export const SESSION_FILLS: { pre: string; post: string } = {
  pre: "rgba(255, 152, 0, 0.10)",
  post: "rgba(41, 98, 255, 0.08)",
};

/** hour/minute in ET for a unix-seconds timestamp. Intl rather than a
 * luxon DateTime per bar: this runs for every bar on every data change,
 * and one cached formatter is an order of magnitude cheaper. */
const ET_CLOCK = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function sessionKind(unixSeconds: number): SessionKind {
  const parts = ET_CLOCK.formatToParts(new Date(unixSeconds * 1000));
  let minutes = 0;
  for (const part of parts) {
    // "24" is what hour12: false yields for midnight in some engines.
    if (part.type === "hour") minutes += (Number(part.value) % 24) * 60;
    if (part.type === "minute") minutes += Number(part.value);
  }
  if (minutes < REGULAR_OPEN_MINUTES) return "pre";
  if (minutes < REGULAR_CLOSE_MINUTES) return "regular";
  return "post";
}

/** The options that make a Histogram series a full-height background wash:
 * its own overlay scale, a hard 0..1 range with no margins (so a value-1
 * column spans the pane), no labels, no crosshair participation. */
export function sessionBandOptions(fill: string): HistogramSeriesPartialOptions {
  return {
    priceScaleId: "session-bands",
    color: fill,
    base: 0,
    priceLineVisible: false,
    lastValueVisible: false,
    autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }),
  };
}

/** Data for one band: a full-height column on the bars inside `kind`'s
 * session, a whitespace slot everywhere else. An empty `times` (a daily
 * chart, say) clears the band. */
export function sessionBandData(
  times: UTCTimestamp[],
  kind: SessionKind,
): (HistogramData | WhitespaceData)[] {
  return times.map((time) =>
    sessionKind(time) === kind ? { time, value: 1 } : { time },
  );
}
