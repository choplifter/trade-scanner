import { useState } from "react";

import { runZeroDteBacktest } from "../../api/playbooks";
import type { ZeroDteRequest, ZeroDteResult } from "../../types/playbooks";
import { formatExpiry } from "../../utils/occ";
import { NATENBERG, withBook } from "./bookRefs";

const ENTRIES = ["09:45", "10:00", "10:30", "11:00", "12:00", "13:00", "14:00", "15:00"];

export interface ZeroDteForm {
  entry: string;
  shortDelta: string;
  width: string;
  /** Blank: no stop. */
  stop: string;
  /** Blank: no target. A share of the credit, in per cent. */
  target: string;
  slippage: string;
  fee: string;
  start: string;
  end: string;
}

// The first setup tested (0.10 delta, $2, 10:00), not the grid's best -- a
// default picked from hindsight would bake the overfitting in -- but held to
// the close: a 2x stop on an 8-cent credit fires on ordinary noise and was
// the worst choice in every corner of the grid, so it made a poor first look.
export const DEFAULT_FORM: ZeroDteForm = {
  entry: "10:00",
  shortDelta: "0.10",
  width: "2",
  stop: "",
  target: "",
  slippage: "0.01",
  fee: "0.03",
  start: "",
  end: "",
};

/** The form as the request: blanks for stop and target mean none, blank
 * dates the backend's whole range. Null when a number is not one. */
export function requestFromForm(f: ZeroDteForm): ZeroDteRequest | null {
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  const delta = num(f.shortDelta);
  const width = num(f.width);
  const stop = num(f.stop);
  const target = num(f.target);
  const slippage = num(f.slippage);
  const fee = num(f.fee);
  const bad = (v: number | null, lo: number, hi: number, optional = false) =>
    v == null ? !optional : !Number.isFinite(v) || v <= lo || v > hi;
  if (bad(delta, 0, 0.5) || bad(width, 0, 20) || bad(stop, 1, 20, true) || bad(target, 0, 99, true)) return null;
  if (slippage == null || !Number.isFinite(slippage) || slippage < 0 || fee == null || !Number.isFinite(fee) || fee < 0) return null;
  return {
    entry: f.entry,
    short_delta: delta!,
    width: width!,
    stop_mult: stop,
    take_profit: target == null ? null : target / 100,
    slippage,
    fee,
    ...(f.start ? { start: f.start } : {}),
    ...(f.end ? { end: f.end } : {}),
  };
}

const money = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : `${v < 0 ? "−" : v > 0 ? "+" : ""}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const tone = (v: number | null | undefined) => (v == null ? undefined : v > 0 ? "delta-up" : v < 0 ? "delta-down" : undefined);

/** Cumulative P/L per spread, as a small SVG line with the zero line. */
function EquityCurve({ points }: { points: { day: string; equity: number }[] }) {
  const W = 600;
  const H = 160;
  const PAD = { l: 52, r: 8, t: 8, b: 18 };
  if (points.length < 2) return null;
  const values = points.map((p) => p.equity);
  const yMin = Math.min(0, ...values);
  const yMax = Math.max(0, ...values);
  const span = yMax - yMin || 1;
  const x = (i: number) => PAD.l + (i / (points.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((yMax - v) / span) * (H - PAD.t - PAD.b);
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const ticks = [0, Math.floor((points.length - 1) / 2), points.length - 1];
  return (
    <svg className="pb-bt-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Cumulative P/L per spread">
      <line className="pb-bt-axis" x1={PAD.l} x2={W - PAD.r} y1={y(0)} y2={y(0)} />
      {[yMax, 0, yMin].filter((v, i, a) => a.indexOf(v) === i).map((v) => (
        <text key={v} className="pb-bt-tick" x={PAD.l - 4} y={y(v) + 4} textAnchor="end">
          {money(v)}
        </text>
      ))}
      {ticks.map((i) => (
        <text key={i} className="pb-bt-tick" x={x(i)} y={H - 4} textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}>
          {formatExpiry(points[i].day)}
        </text>
      ))}
      <path className={values[values.length - 1] >= 0 ? "pb-bt-equity" : "pb-bt-equity negative"} d={line} />
    </svg>
  );
}

const CAVEATS_TEXT =
  "Real SPY option minute bars from Alpaca since Feb 2024 -- trades, not quotes: Alpaca keeps no historical bid/ask for options, so a fill is the last print and the crossing cost is the slippage set here. Delta at entry comes from the IV solved out of that print. A close between the strikes means assignment of SPY shares in reality; it is counted as 'pinned' and priced as a clean expiry. The full grid (2,688 settings) found no setup whose 2024-25 result carried into 2026: read a good run here as one draw, not as proof.";
const CAVEATS = withBook(CAVEATS_TEXT, NATENBERG.ivAsPredictor, NATENBERG.verticals);

/** The 0DTE put credit spread backtest (backend app/options/zero_dte.py):
 * a parameter form, the summary, the years, the equity curve and the worst
 * days. Collapsed until opened; the first run fetches the bars and takes
 * minutes, later ones read the cache. */
export function ZeroDtePanel() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<ZeroDteForm>(DEFAULT_FORM);
  const [result, setResult] = useState<ZeroDteResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = requestFromForm(form);
  const set = (key: keyof ZeroDteForm) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const run = async () => {
    if (!request) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await runZeroDteBacktest(request));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const r = result;
  return (
    <section className="pb-daily zdte">
      <div className="pb-start-head">
        <button type="button" className="timeframe-button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          0DTE backtest (SPY) {open ? "▾" : "▸"}
        </button>
        <span className="order-hint" title={CAVEATS}>
          Same-day put credit spreads on real option bars · research only
        </span>
      </div>
      {open && (
        <>
          <div className="zdte-form">
            <label title="When the spread is opened each day, New York time.">
              Entry{" "}
              <select value={form.entry} onChange={set("entry")}>
                {ENTRIES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label title="The short put is the one nearest this delta at entry.">
              Short Δ <input type="number" step={0.01} min={0.01} max={0.5} value={form.shortDelta} onChange={set("shortDelta")} />
            </label>
            <label title="Dollars between the short and the long put.">
              Width $ <input type="number" step={1} min={1} max={20} value={form.width} onChange={set("width")} />
            </label>
            <label title="Close when the spread costs this many times the credit. Blank: no stop, held to the close.">
              Stop × <input type="number" step={0.5} min={1.5} max={20} value={form.stop} placeholder="none" onChange={set("stop")} />
            </label>
            <label title="Close once this share of the credit is earned. Blank: no target.">
              Target % <input type="number" step={10} min={10} max={95} value={form.target} placeholder="none" onChange={set("target")} />
            </label>
            <label title="Crossing cost per leg each way, per share.">
              Slippage <input type="number" step={0.005} min={0} max={0.5} value={form.slippage} onChange={set("slippage")} />
            </label>
            <label title="Fees per contract per leg each way, in dollars (regulatory and clearing).">
              Fee $ <input type="number" step={0.01} min={0} max={2} value={form.fee} onChange={set("fee")} />
            </label>
            <label title="First day; blank: Feb 2024, where Alpaca's option bars begin.">
              From <input type="date" value={form.start} onChange={set("start")} />
            </label>
            <label title="Last day; blank: yesterday.">
              To <input type="date" value={form.end} onChange={set("end")} />
            </label>
            <button type="button" className="generate-button" disabled={loading || !request} onClick={() => void run()}>
              {loading ? "Running…" : "Run backtest"}
            </button>
            <button type="button" className="link-button" onClick={() => setForm(DEFAULT_FORM)}>
              reset
            </button>
          </div>
          {!request && <p className="order-rejection">Check the numbers: delta 0–0.5, width up to 20, stop above 1×, target under 100 %.</p>}
          {loading && <p className="order-hint">Days not yet cached are fetched from Alpaca first; a full first run takes a few minutes.</p>}
          {error && <p className="order-rejection">{error}</p>}
          <p className="pb-daily-warning" title={CAVEATS}>
            {CAVEATS_TEXT}
          </p>
          {r && r.trades === 0 && <p className="order-hint">No trades in this range ({r.days} sessions, {r.no_data} without data).</p>}
          {r && r.trades > 0 && (
            <>
              <div className="key-figures" role="group" aria-label="0DTE backtest summary">
                <span className={`key-figure ${(r.total ?? 0) > 0 ? "good" : "bad"}`} title="Sum of every trade's P/L for one spread, after slippage and fees.">
                  <span className="key-figure-label">Total</span> {money(r.total)}
                </span>
                <span className="key-figure" title={`${r.trades} trades out of ${r.days} sessions; days skipped had no strike near the delta or too little credit.`}>
                  <span className="key-figure-label">Trades</span> {r.trades}
                </span>
                <span className="key-figure" title="Share of trades that made money.">
                  <span className="key-figure-label">Wins</span> {((r.win_rate ?? 0) * 100).toFixed(0)} %
                </span>
                <span className="key-figure" title="Average winning and losing trade.">
                  <span className="key-figure-label">Avg</span> <span className="delta-up">{money(r.avg_win, 2)}</span> /{" "}
                  <span className="delta-down">{money(r.avg_loss, 2)}</span>
                </span>
                <span className="key-figure" title="Gross profit over gross loss; above 1 the wins paid for the losses.">
                  <span className="key-figure-label">Profit factor</span> {r.profit_factor?.toFixed(2) ?? "—"}
                </span>
                <span className="key-figure bad" title="The deepest fall of the cumulative P/L from a high.">
                  <span className="key-figure-label">Max drawdown</span> {money(r.max_drawdown)}
                </span>
                <span className="key-figure" title="Average credit per spread at entry, after slippage.">
                  <span className="key-figure-label">Credit</span> {money(r.avg_credit, 2)}
                </span>
                <span className="key-figure" title="Closed by the stop / the target before the close.">
                  <span className="key-figure-label">Stops / targets</span> {r.stops ?? 0} / {r.targets ?? 0}
                </span>
                <span className={`key-figure ${(r.pinned ?? 0) > 0 ? "bad" : ""}`} title="Closes between the strikes: in reality the short put is assigned and SPY shares are held overnight. Priced here as a clean expiry.">
                  <span className="key-figure-label">Pinned</span> {r.pinned ?? 0}
                </span>
              </div>
              {r.equity && <EquityCurve points={r.equity} />}
              {r.by_year && (
                <table className="opt-table zdte-years">
                  <thead>
                    <tr>
                      <th scope="col">Year</th>
                      <th scope="col">Trades</th>
                      <th scope="col">Wins</th>
                      <th scope="col">P/L</th>
                      <th scope="col">Worst day</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(r.by_year).map(([year, y]) => (
                      <tr key={year}>
                        <th scope="row">{year}</th>
                        <td>{y.trades}</td>
                        <td>{y.trades ? `${((y.wins / y.trades) * 100).toFixed(0)} %` : "—"}</td>
                        <td className={tone(y.pnl)}>{money(y.pnl)}</td>
                        <td className={tone(y.worst)}>{money(y.worst)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {r.worst && r.worst.length > 0 && (
                <>
                  <h4>Worst days</h4>
                  <table className="opt-table zdte-worst">
                    <thead>
                      <tr>
                        <th scope="col">Day</th>
                        <th scope="col">Spread</th>
                        <th scope="col">Credit</th>
                        <th scope="col">Exit</th>
                        <th scope="col">SPY entry → exit</th>
                        <th scope="col">P/L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.worst.map((t) => (
                        <tr key={t.day}>
                          <th scope="row">{formatExpiry(t.day)}</th>
                          <td>
                            {t.short_strike}/{t.long_strike}P · Δ {Math.abs(t.short_delta).toFixed(2)}
                          </td>
                          <td>{t.credit.toFixed(2)}</td>
                          <td>
                            {t.exit} {t.exit_at}
                            {t.pinned ? " · pinned" : ""}
                          </td>
                          <td>
                            {t.spot.toFixed(2)} → {t.close.toFixed(2)}
                          </td>
                          <td className={tone(t.pnl)}>{money(t.pnl, 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
              {r.no_data > 0 && <p className="order-hint">{r.no_data} session(s) without option bars were left out.</p>}
            </>
          )}
        </>
      )}
    </section>
  );
}
