import { TickMarkType, type Time } from "lightweight-charts";

/**
 * Axis and crosshair time formatting, shared by every lightweight-charts
 * surface so they all read in the same timezone and the same style.
 *
 * lightweight-charts formats its labels using the Date object's UTC getters,
 * so by default every chart shows UTC regardless of where the viewer is.
 * Intl.DateTimeFormat with no explicit `timeZone` uses the browser's local
 * timezone, so overriding the formatters with it makes a chart display in
 * whatever timezone the viewer is actually in.
 */
const TIME_FORMAT = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const DAY_FORMAT = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short" });
const MONTH_FORMAT = new Intl.DateTimeFormat(undefined, { month: "short", year: "numeric" });
const YEAR_FORMAT = new Intl.DateTimeFormat(undefined, { year: "numeric" });
const CROSSHAIR_FORMAT = new Intl.DateTimeFormat(undefined, {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** Axis tick labels, at whatever granularity the time scale asks for. */
export function tickMarkFormatter(time: Time, tickMarkType: TickMarkType): string {
  const date = new Date((time as number) * 1000);
  switch (tickMarkType) {
    case TickMarkType.Year:
      return YEAR_FORMAT.format(date);
    case TickMarkType.Month:
      return MONTH_FORMAT.format(date);
    case TickMarkType.DayOfMonth:
      return DAY_FORMAT.format(date);
    default:
      return TIME_FORMAT.format(date);
  }
}

/** The crosshair's time label: date and time, since the crosshair is read
 * against a single point rather than a whole axis. */
export function crosshairTimeFormatter(time: Time): string {
  return CROSSHAIR_FORMAT.format(new Date((time as number) * 1000));
}

/** A date-only crosshair, for series sampled once per session -- showing
 * "00:00" against a daily close says nothing and reads like a real time. */
export function crosshairDateFormatter(time: Time): string {
  return DAY_FORMAT.format(new Date((time as number) * 1000));
}
