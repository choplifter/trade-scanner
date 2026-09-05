/**
 * Every clock the app shows, in one time zone: the browser's, or New York's
 * (Settings → Display → Time zone). Formatters are built per zone on first
 * use and cached, and every function reads the setting at call time, so a
 * change in the dialog reaches the next render without anyone re-wiring.
 *
 * Only display goes through here. Session boundaries (premarket, the bell,
 * the close), the journal's entry windows and the trading windows are
 * market concepts and stay computed in New York time whatever is shown --
 * this module converts their labels, not their logic.
 */

import { MARKET_TIME_ZONE, displayTimeZone } from "../api/settings";

type Kind = "clock" | "clockSeconds" | "dateTime" | "dateTimeNumeric" | "weekdayDateTime" | "dayKey" | "zoneName";

const OPTIONS: Record<Kind, Intl.DateTimeFormatOptions> = {
  clock: { hour: "2-digit", minute: "2-digit", hour12: false },
  clockSeconds: { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false },
  dateTime: { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false },
  dateTimeNumeric: { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false },
  weekdayDateTime: { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false },
  dayKey: { year: "numeric", month: "2-digit", day: "2-digit" },
  zoneName: { timeZoneName: "short" },
};

const cache = new Map<string, Intl.DateTimeFormat>();

function formatter(kind: Kind, timeZone: string | undefined, locale?: string): Intl.DateTimeFormat {
  const key = `${kind}|${timeZone ?? ""}|${locale ?? ""}`;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(locale, { ...OPTIONS[kind], ...(timeZone ? { timeZone } : {}) });
    cache.set(key, f);
  }
  return f;
}

function toDate(value: string | number | Date): Date {
  return value instanceof Date ? value : new Date(value);
}

/** "15:49" */
export function formatClock(value: string | number | Date): string {
  return formatter("clock", displayTimeZone()).format(toDate(value));
}

/** "15:49:07" */
export function formatClockSeconds(value: string | number | Date): string {
  return formatter("clockSeconds", displayTimeZone()).format(toDate(value));
}

/** "08 Sep 15:49" */
export function formatDateTime(value: string | number | Date): string {
  return formatter("dateTime", displayTimeZone()).format(toDate(value)).replace(",", "");
}

/** "08.09. 15:49" (or the locale's numeric equivalent) */
export function formatDateTimeNumeric(value: string | number | Date): string {
  return formatter("dateTimeNumeric", displayTimeZone()).format(toDate(value)).replace(",", "");
}

/** "Tue 08.09. 15:49" */
export function formatWeekdayDateTime(value: string | number | Date): string {
  return formatter("weekdayDateTime", displayTimeZone()).format(toDate(value)).replace(",", "");
}

/** "2026-09-08" in the display zone -- for "is this the same day" checks. */
export function dayKey(value: string | number | Date): string {
  return formatter("dayKey", displayTimeZone(), "en-CA").format(toDate(value));
}

/** "ET" when showing New York, else the browser zone's short name
 * ("MESZ", "GMT+2", …) -- for the one label beside a table of times. */
export function timeZoneLabel(): string {
  return displayTimeZone() === MARKET_TIME_ZONE ? "ET" : browserTimeZoneName();
}

export function browserTimeZoneName(): string {
  return formatter("zoneName", undefined).formatToParts(new Date()).find((p) => p.type === "timeZoneName")?.value ?? "local";
}

/** Minutes after midnight of `value` in the given zone. */
function minutesOfDay(value: Date, timeZone: string | undefined): number {
  const parts = formatter("clock", timeZone, "en-US").formatToParts(value);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0) % 24;
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  return hour * 60 + minute;
}

/** A New York clock time (minutes after midnight ET) as the same instant's
 * clock time in the display zone -- "09:30" becomes "15:30" for a viewer
 * in Berlin. Uses today's offset, which is what a label about "the open"
 * means to the reader today. */
export function marketMinutesToDisplay(minutesEt: number): number {
  const tz = displayTimeZone();
  if (tz === MARKET_TIME_ZONE) return minutesEt;
  const now = new Date();
  const shift = minutesOfDay(now, tz) - minutesOfDay(now, MARKET_TIME_ZONE);
  return (((minutesEt + shift) % 1440) + 1440) % 1440;
}

/** "15:30" for minutes after midnight. */
export function clockFromMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}
