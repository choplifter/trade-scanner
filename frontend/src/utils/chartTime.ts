import { TickMarkType, type Time } from "lightweight-charts";

import { displayTimeZone } from "../api/settings";

/**
 * Axis and crosshair time formatting, shared by every lightweight-charts
 * surface so they all read in the same timezone and the same style.
 *
 * lightweight-charts formats its labels using the Date object's UTC getters,
 * so by default every chart shows UTC regardless of where the viewer is.
 * These formatters use Intl.DateTimeFormat in the zone the settings ask
 * for (Settings -> Display -> Time zone): the browser's, or New York's.
 * The zone is read on every call and the formatters cached per zone, so a
 * change in the dialog shows on the next redraw; CandleChart re-applies
 * the formatters on that change to force one.
 */
type Kind = "time" | "day" | "month" | "year" | "crosshair";

const OPTIONS: Record<Kind, Intl.DateTimeFormatOptions> = {
  time: { hour: "2-digit", minute: "2-digit", hour12: false },
  day: { day: "2-digit", month: "short" },
  month: { month: "short", year: "numeric" },
  year: { year: "numeric" },
  crosshair: { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false },
};

const cache = new Map<string, Intl.DateTimeFormat>();

function fmt(kind: Kind): Intl.DateTimeFormat {
  const timeZone = displayTimeZone();
  const key = `${kind}|${timeZone ?? ""}`;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(undefined, { ...OPTIONS[kind], ...(timeZone ? { timeZone } : {}) });
    cache.set(key, f);
  }
  return f;
}

/** Axis tick labels, at whatever granularity the time scale asks for. */
export function tickMarkFormatter(time: Time, tickMarkType: TickMarkType): string {
  const date = new Date((time as number) * 1000);
  switch (tickMarkType) {
    case TickMarkType.Year:
      return fmt("year").format(date);
    case TickMarkType.Month:
      return fmt("month").format(date);
    case TickMarkType.DayOfMonth:
      return fmt("day").format(date);
    default:
      return fmt("time").format(date);
  }
}

/** The crosshair's time label: date and time, since the crosshair is read
 * against a single point rather than a whole axis. */
export function crosshairTimeFormatter(time: Time): string {
  return fmt("crosshair").format(new Date((time as number) * 1000));
}

/** A date-only crosshair, for series sampled once per session -- showing
 * "00:00" against a daily close says nothing and reads like a real time. */
export function crosshairDateFormatter(time: Time): string {
  return fmt("day").format(new Date((time as number) * 1000));
}
