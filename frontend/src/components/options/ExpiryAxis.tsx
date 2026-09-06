import { useMemo, type MouseEvent } from "react";

import { useDragScroll } from "../../hooks/useDragScroll";
import type { ExpiryInfo } from "../../types/options";
import { formatExpiry, weekdayOf } from "../../utils/occ";
import type { ExpiryMarks } from "./eventMarks";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

interface ExpiryAxisProps {
  expiries: ExpiryInfo[];
  /** The ticket's expiry (the short leg's for a calendar/diagonal). */
  expiry: string | null;
  /** The long leg's expiry of a calendar/diagonal; null otherwise. */
  longExpiry: string | null;
  /** Which leg a click moves when there are two. */
  picking: "short" | "long";
  marks: Map<string, ExpiryMarks>;
  earningsDate: string | null;
  onSelect: (expiry: string) => void;
  onSelectLong: (expiry: string) => void;
  hint?: string | null;
}

interface MonthGroup {
  key: string;
  label: string;
  days: ExpiryInfo[];
}

/** Expiries grouped by month, oldest first; the label carries the year at
 * every year change (Jan '27) so a board two years deep stays readable. */
export function groupByMonth(expiries: ExpiryInfo[]): MonthGroup[] {
  const groups: MonthGroup[] = [];
  let lastYear: number | null = new Date().getFullYear();
  for (const e of expiries) {
    const [y, m] = e.expiry.split("-").map(Number);
    const key = `${y}-${m}`;
    let group = groups[groups.length - 1];
    if (!group || group.key !== key) {
      const month = MONTHS[m - 1] ?? String(m);
      const label = y !== lastYear ? `${month} '${String(y).slice(-2)}` : month;
      lastYear = y;
      group = { key, label, days: [] };
      groups.push(group);
    }
    group.days.push(e);
  }
  return groups;
}

/**
 * The expiry axis, the way a strategy builder draws it: a band of months,
 * the listed days beneath as chips, the whole board out to the LEAPS in
 * one line. The ticket's expiry is the pressed chip; a calendar's or
 * diagonal's long leg is marked in its own colour, and a click moves the
 * leg being picked (Shift moves the other one). Earnings and macro dates
 * sit on their first held expiry as dots, spelled out on hover. Dragged
 * sideways when it overflows.
 */
export function ExpiryAxis({ expiries, expiry, longExpiry, picking, marks, earningsDate, onSelect, onSelectLong, hint }: ExpiryAxisProps) {
  const drag = useDragScroll<HTMLDivElement>();
  const groups = useMemo(() => groupByMonth(expiries), [expiries]);
  const twoLegs = longExpiry !== null;
  const shortInfo = expiries.find((e) => e.expiry === expiry) ?? null;
  const longInfo = twoLegs ? expiries.find((e) => e.expiry === longExpiry) ?? null : null;

  const legHint = twoLegs ? `click moves the ${picking} leg, ⇧ the other` : null;

  const click = (e: MouseEvent<HTMLButtonElement>, day: ExpiryInfo) => {
    if (!twoLegs) {
      onSelect(day.expiry);
      return;
    }
    const long = e.shiftKey ? picking !== "long" : picking === "long";
    if (long) onSelectLong(day.expiry);
    else onSelect(day.expiry);
  };

  const title = (day: ExpiryInfo): string => {
    const m = marks.get(day.expiry);
    return [
      `${weekdayOf(day.expiry)} ${formatExpiry(day.expiry)} ${day.expiry.slice(0, 4)} · ${day.dte}d · ${day.contract_count} contracts`,
      day.expiry === expiry ? (twoLegs ? "short leg's expiry" : "the ticket's expiry") : null,
      day.expiry === longExpiry ? "long leg's expiry" : null,
      m?.earnings && earningsDate ? `first expiry held through earnings on ${formatExpiry(earningsDate)}` : null,
      ...(m?.macro ?? []).map((x) => `${x.label} ${formatExpiry(x.date)} (${x.event})`),
      legHint,
    ]
      .filter(Boolean)
      .join(" · ");
  };

  return (
    <div className="expiry-axis-wrap">
      <div className="expiry-axis-head order-hint">
        <span className="expiry-axis-label">Expiries</span>
        {shortInfo && (
          <span className="expiry-axis-dte short" title={twoLegs ? "short leg" : "days to expiry"}>
            {shortInfo.dte}d
          </span>
        )}
        {longInfo && (
          <span className="expiry-axis-dte long" title="long leg">
            {longInfo.dte}d
          </span>
        )}
        {legHint && <span className="expiry-axis-hint">{legHint}</span>}
        {hint && <span className="expiry-axis-hint">{hint}</span>}
      </div>
      <div className="expiry-axis" {...drag}>
        {groups.map((g) => (
          <div key={g.key} className="expiry-month">
            <div className="expiry-month-label">{g.label}</div>
            <div className="expiry-days">
              {g.days.map((day) => {
                const m = marks.get(day.expiry);
                const isShort = day.expiry === expiry;
                const isLong = day.expiry === longExpiry;
                return (
                  <button
                    key={day.expiry}
                    type="button"
                    className={`expiry-day${isLong ? " long" : ""}${twoLegs && isShort ? " short" : ""}`}
                    aria-pressed={isShort}
                    data-expiry={day.expiry}
                    onClick={(e) => click(e, day)}
                    title={title(day)}
                  >
                    {Number(day.expiry.slice(-2))}
                    {(m?.earnings || (m?.macro.length ?? 0) > 0) && (
                      <span className="expiry-dots" aria-hidden="true">
                        {m?.earnings && <span className="expiry-dot earnings" />}
                        {m?.macro.map((x) => <span key={`${x.date}-${x.label}`} className="expiry-dot macro" />)}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
