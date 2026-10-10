import { Fragment, useCallback, useEffect, useState } from "react";

import { getJournalEntries, saveJournalEntry } from "../../api/http";
import { getClosedSpreads } from "../../api/options";
import type { TradingMode } from "../../api/tradingMode";
import type { JournalEntry } from "../../types/journal";
import type { ClosedSpread, ClosedSpreadsResponse } from "../../types/options";
import { STRATEGY_LABELS } from "../../types/options";
import { formatExpiry, formatStrike } from "../../utils/occ";
import { JournalEditor } from "../trading/TradeJournalWidget";

const RANGES = [
  { key: "day", label: "Today" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "all", label: "All" },
] as const;

const money = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : `${v < 0 ? "−" : v > 0 ? "+" : ""}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const tone = (v: number | null | undefined) => (v == null ? undefined : v > 0 ? "delta-up" : v < 0 ? "delta-down" : undefined);

export function strategyName(strategy: string): string {
  const named = (STRATEGY_LABELS as Record<string, string>)[strategy];
  if (named) return named;
  return strategy.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** "−773P/+771P" -- each leg's side and strike, puts before calls. */
export function strikesLabel(spread: ClosedSpread): string {
  return spread.legs.map((l) => `${l.side === "short" ? "−" : "+"}${formatStrike(l.strike)}${l.kind === "call" ? "C" : "P"}`).join("/");
}

/** "0.09 cr" / "1.20 db" -- the per-share net as a trader says it. */
export function netLabel(net: number): string {
  if (Math.abs(net) < 0.005) return "0.00";
  return `${Math.abs(net).toFixed(2)} ${net < 0 ? "cr" : "db"}`;
}

/** The exit as the closing trade: the opposite of what is held, so a
 * credit spread's value (-2.94 as held) was a 2.94 debit to close. */
export function exitLabel(exitNet: number): string {
  return netLabel(-exitNet);
}

function when(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** The closed option spreads -- backend app/options/closed_spreads.py: the
 * per-contract round trips put back together, how each ended, the day and
 * week subtotals -- and the options journal: a note, a 1-5 rating and tags
 * on each spread, kept in the same journal as the stock trades. */
export function ClosedSpreadsTab({ mode }: { mode: TradingMode }) {
  const [range, setRange] = useState<string>("week");
  const [data, setData] = useState<ClosedSpreadsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [entries, setEntries] = useState<Record<string, JournalEntry>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [withNotes, setWithNotes] = useState(false);
  const [byWeek, setByWeek] = useState(true);

  const load = useCallback(() => {
    getClosedSpreads(range)
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    getJournalEntries()
      .then((res) => setEntries(res.entries))
      .catch(() => undefined);
  }, [range]);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, [load, mode]);

  const spreads = (data?.spreads ?? []).filter((s) => !withNotes || entries[s.id]);
  const s = data?.summary;
  const buckets = byWeek ? (data?.weeks ?? []) : (data?.days ?? []);

  return (
    <div className="closed-spreads">
      <div className="pb-start-head">
        <span className="timeframe-selector" role="group" aria-label="Range">
          {RANGES.map((r) => (
            <button key={r.key} type="button" className="timeframe-button" aria-pressed={range === r.key} onClick={() => setRange(r.key)}>
              {r.label}
            </button>
          ))}
        </span>
        <label className="order-hint" title="Only the spreads you have written a journal note on">
          <input type="checkbox" checked={withNotes} onChange={(e) => setWithNotes(e.target.checked)} /> with notes only
        </label>
        <button type="button" className="link-button" onClick={load}>
          refresh
        </button>
      </div>
      {error && <p className="order-rejection">{error}</p>}
      {s && s.count > 0 && (
        <div className="key-figures" role="group" aria-label="Closed spreads summary">
          <span className={`key-figure ${s.pnl > 0 ? "good" : s.pnl < 0 ? "bad" : ""}`} title="Realised P/L of the spreads closed in this range, after fills.">
            <span className="key-figure-label">P/L</span> {money(s.pnl)}
          </span>
          <span className="key-figure">
            <span className="key-figure-label">Spreads</span> {s.count}
          </span>
          <span className="key-figure">
            <span className="key-figure-label">Wins</span> {s.wins} / {s.count} ({((s.win_rate ?? 0) * 100).toFixed(0)} %)
          </span>
          <span className="key-figure" title="Average winning and losing spread.">
            <span className="key-figure-label">Avg</span> <span className="delta-up">{money(s.avg_win)}</span> / <span className="delta-down">{money(s.avg_loss)}</span>
          </span>
          <span className="key-figure">
            <span className="key-figure-label">Best / worst</span> {money(s.best)} / {money(s.worst)}
          </span>
        </div>
      )}
      {buckets.length > 0 && (
        <>
          <div className="pb-start-head">
            <span className="timeframe-selector" role="group" aria-label="Subtotals by">
              <button type="button" className="timeframe-button" aria-pressed={byWeek} onClick={() => setByWeek(true)}>
                By week
              </button>
              <button type="button" className="timeframe-button" aria-pressed={!byWeek} onClick={() => setByWeek(false)}>
                By day
              </button>
            </span>
          </div>
          <table className="opt-table closed-buckets">
            <thead>
              <tr>
                <th scope="col">{byWeek ? "Week of" : "Day"}</th>
                <th scope="col">Spreads</th>
                <th scope="col">Wins</th>
                <th scope="col">P/L</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map((b) => {
                const key = (byWeek ? b.week_start : b.date) ?? "";
                return (
                  <tr key={key}>
                    <th scope="row">{new Date(`${key}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short" })}</th>
                    <td>{b.count}</td>
                    <td>{b.wins}</td>
                    <td className={tone(b.pnl)}>{money(b.pnl)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
      {data && spreads.length === 0 && (
        <p className="order-hint">{withNotes ? "No spread with a journal note in this range." : "No option spread closed in this range."}</p>
      )}
      {spreads.length > 0 && (
        <table className="performance-table closed-spreads-table">
          <thead>
            <tr>
              <th>Closed</th>
              <th>Symbol</th>
              <th>Strategy</th>
              <th>Strikes</th>
              <th>Expiry</th>
              <th>Qty</th>
              <th title="Per share at entry: cr received, db paid">Entry</th>
              <th title="Per share, the closing trade: db paid to close, cr received; 0.00 when it expired worthless">Exit</th>
              <th>Ended</th>
              <th>P&amp;L</th>
              <th title="Journal note">Journal</th>
            </tr>
          </thead>
          <tbody>
            {spreads.map((sp) => {
              const entry = entries[sp.id];
              const isOpen = open === sp.id;
              return (
                <Fragment key={sp.id}>
                  <tr aria-selected={isOpen} onClick={() => setOpen(isOpen ? null : sp.id)}>
                    <td>{when(sp.closed_at)}</td>
                    <td className="symbol-cell">{sp.underlying}</td>
                    <td>{strategyName(sp.strategy)}</td>
                    <td>{strikesLabel(sp)}</td>
                    <td>{formatExpiry(sp.expiry)}</td>
                    <td>{sp.qty}</td>
                    <td>{netLabel(sp.entry_net)}</td>
                    <td>{exitLabel(sp.exit_net)}</td>
                    <td>{sp.outcome}</td>
                    <td className={tone(sp.pnl)}>{money(sp.pnl, 2)}</td>
                    <td title={entry?.note || undefined}>
                      {entry ? `${entry.rating ? "★".repeat(entry.rating) : "✎"}${entry.tags.length ? ` ${entry.tags.join(", ")}` : ""}` : "—"}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={11}>
                        <ul className="spread-legs">
                          {sp.legs.map((l) => (
                            <li key={l.trade_id}>
                              {l.side === "short" ? "Sold" : "Bought"} {l.qty} × {l.symbol} @ {l.entry.toFixed(2)} → {l.exit.toFixed(2)} · {l.outcome}{" "}
                              {when(l.closed_at)} · <span className={tone(l.pnl)}>{money(l.pnl, 2)}</span>
                            </li>
                          ))}
                        </ul>
                        <p className="order-hint">
                          Opened {when(sp.opened_at)} · held {sp.held_days < 1 ? `${Math.round(sp.held_days * 24 * 10) / 10} h` : `${sp.held_days.toFixed(1)} days`}
                        </p>
                        {editing === sp.id ? (
                          <JournalEditor
                            entry={entry}
                            onCancel={() => setEditing(null)}
                            onSave={async (draft) => {
                              const res = await saveJournalEntry(sp.id, draft);
                              setEntries((prev) => ({ ...prev, [sp.id]: res.entry }));
                              setEditing(null);
                            }}
                          />
                        ) : (
                          <div className="closed-journal">
                            {entry?.note ? <p className="closed-journal-note">{entry.note}</p> : null}
                            <button type="button" className="row-action" onClick={() => setEditing(sp.id)}>
                              {entry ? "Edit journal" : "Write journal"}
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
