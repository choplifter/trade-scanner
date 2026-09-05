import { useMemo, useState } from "react";

import type { Trade } from "../../types/trading";
import { formatMoney } from "../../utils/format";
import { parseOcc } from "../../utils/occ";
import { clockFromMinutes, marketMinutesToDisplay, timeZoneLabel } from "../../utils/time";

const ET = "America/New_York";

interface Bucket {
  key: string;
  label: string;
  n: number;
  wins: number;
  pnl: number;
  best: number;
  worst: number;
}

interface Keyed {
  key: string;
  label: string;
}

/** Hour, minute and weekday of an instant in New York time. */
function etParts(iso: string): { minutes: number; weekday: string; date: string } | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const hour = Number(get("hour")) % 24;
  const minute = Number(get("minute"));
  return { minutes: hour * 60 + minute, weekday: get("weekday"), date: `${get("year")}-${get("month")}-${get("day")}` };
}

// The windows are market time: the bell, the first half hour, lunch, the
// close. Their labels are shown in whatever zone the settings display
// (utils/time.ts), so a Berlin viewer reads "15:30 – 16:00" for the open.
const ENTRY_WINDOWS: { key: string; from: number; to: number }[] = [
  { key: "pre", from: 0, to: 9 * 60 + 30 },
  { key: "0930", from: 9 * 60 + 30, to: 10 * 60 },
  { key: "1000", from: 10 * 60, to: 10 * 60 + 30 },
  { key: "1030", from: 10 * 60 + 30, to: 11 * 60 },
  { key: "1100", from: 11 * 60, to: 12 * 60 },
  { key: "1200", from: 12 * 60, to: 14 * 60 },
  { key: "1400", from: 14 * 60, to: 15 * 60 + 30 },
  { key: "1530", from: 15 * 60 + 30, to: 16 * 60 },
  { key: "post", from: 16 * 60, to: 24 * 60 },
];

function windowLabel(w: { key: string; from: number; to: number }): string {
  const at = (minutes: number) => clockFromMinutes(marketMinutesToDisplay(minutes));
  if (w.key === "pre") return `before ${at(w.to)}`;
  if (w.key === "post") return `after ${at(w.from)}`;
  return `${at(w.from)} – ${at(w.to)}`;
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];

function entryWindow(minutes: number): Keyed {
  const w = ENTRY_WINDOWS.find((x) => minutes >= x.from && minutes < x.to) ?? ENTRY_WINDOWS[ENTRY_WINDOWS.length - 1];
  return { key: w.key, label: windowLabel(w) };
}

function dteBucket(expiry: string, openedDate: string): Keyed {
  const days = Math.round((Date.parse(`${expiry}T12:00:00Z`) - Date.parse(`${openedDate}T12:00:00Z`)) / 86_400_000);
  if (days <= 0) return { key: "0", label: "0DTE" };
  if (days <= 7) return { key: "1", label: "1 – 7 days" };
  return { key: "2", label: "8+ days" };
}

function aggregate(trades: Trade[], keyOf: (t: Trade) => Keyed | null, order?: string[]): Bucket[] {
  const buckets = new Map<string, Bucket>();
  for (const t of trades) {
    const k = keyOf(t);
    if (!k) continue;
    const b = buckets.get(k.key) ?? { key: k.key, label: k.label, n: 0, wins: 0, pnl: 0, best: -Infinity, worst: Infinity };
    b.n += 1;
    if (t.pnl > 0) b.wins += 1;
    b.pnl += t.pnl;
    b.best = Math.max(b.best, t.pnl);
    b.worst = Math.min(b.worst, t.pnl);
    buckets.set(k.key, b);
  }
  const out = [...buckets.values()];
  if (order) out.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
  else out.sort((a, b) => a.key.localeCompare(b.key));
  return out;
}

function underlyingOf(t: Trade): string {
  return parseOcc(t.symbol)?.underlying ?? t.symbol;
}

function BucketTable({ title, rows }: { title: string; rows: Bucket[] }) {
  return (
    <div className="trade-stats-block">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="order-hint">—</p>
      ) : (
        <table className="performance-table trade-stats-table">
          <thead>
            <tr>
              <th />
              <th>Trades</th>
              <th>Win</th>
              <th>P&amp;L</th>
              <th>Avg</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.key}>
                <td>{b.label}</td>
                <td>{b.n}</td>
                <td>{Math.round((b.wins / b.n) * 100)}%</td>
                <td className={b.pnl >= 0 ? "delta-up" : "delta-down"}>{formatMoney(b.pnl)}</td>
                <td className={b.pnl >= 0 ? "delta-up" : "delta-down"}>{formatMoney(b.pnl / b.n)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Where the P&L of one underlying came from: entry time of day (ET),
 * calls vs puts, days to expiry at entry, weekday -- the questions "which
 * entry window works for me on SPY" is answered from the journal's own
 * closed trades. Client-side over the trade list already loaded. */
export function TradeStats({ trades }: { trades: Trade[] }) {
  const underlyings = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of trades) counts.set(underlyingOf(t), (counts.get(underlyingOf(t)) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([u]) => u);
  }, [trades]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const underlying = chosen && underlyings.includes(chosen) ? chosen : underlyings.includes("SPY") ? "SPY" : (underlyings[0] ?? null);

  const selected = useMemo(() => trades.filter((t) => underlyingOf(t) === underlying), [trades, underlying]);
  const stats = useMemo(() => {
    const byWindow = aggregate(
      selected,
      (t) => {
        const p = etParts(t.opened_at);
        return p ? entryWindow(p.minutes) : null;
      },
      ENTRY_WINDOWS.map((w) => w.key),
    );
    const byKind = aggregate(
      selected,
      (t) => {
        const occ = parseOcc(t.symbol);
        if (!occ) return { key: "stock", label: t.side === "long" ? "Shares long" : "Shares short" };
        return occ.kind === "call" ? { key: "call", label: "Calls" } : { key: "put", label: "Puts" };
      },
      ["call", "put", "stock"],
    );
    const byDte = aggregate(
      selected,
      (t) => {
        const occ = parseOcc(t.symbol);
        const p = etParts(t.opened_at);
        return occ && p ? dteBucket(occ.expiry, p.date) : null;
      },
      ["0", "1", "2"],
    );
    const byWeekday = aggregate(
      selected,
      (t) => {
        const p = etParts(t.opened_at);
        return p ? { key: p.weekday, label: p.weekday } : null;
      },
      WEEKDAYS,
    );
    const total = selected.reduce((s, t) => s + t.pnl, 0);
    const wins = selected.filter((t) => t.pnl > 0).length;
    return { byWindow, byKind, byDte, byWeekday, total, wins };
  }, [selected]);

  if (underlyings.length === 0) return null;

  return (
    <div className="trade-stats">
      <div className="trade-stats-head">
        <button type="button" className="row-action" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          Stats {open ? "▾" : "▸"}
        </button>
        <select value={underlying ?? ""} onChange={(e) => setChosen(e.target.value)} title="Underlying">
          {underlyings.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
        <span className="order-hint">
          {selected.length} trades · {selected.length ? Math.round((stats.wins / selected.length) * 100) : 0}% winners ·{" "}
          <span className={stats.total >= 0 ? "delta-up" : "delta-down"}>{formatMoney(stats.total)}</span> · entry
          times in {timeZoneLabel()}
        </span>
      </div>
      {open && (
        <div className="trade-stats-grid">
          <BucketTable title="Entry time" rows={stats.byWindow} />
          <BucketTable title="Calls vs puts" rows={stats.byKind} />
          <BucketTable title="Days to expiry at entry" rows={stats.byDte} />
          <BucketTable title="Weekday" rows={stats.byWeekday} />
        </div>
      )}
    </div>
  );
}
