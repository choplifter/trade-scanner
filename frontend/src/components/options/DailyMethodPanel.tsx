import { useState } from "react";

import { runDailyMethod } from "../../api/playbooks";
import type { CloseSpreadRequest, LoadableStructure, SpreadTicketRequest } from "../../types/options";
import type { DailyMethodResult } from "../../types/playbooks";
import { formatExpiry } from "../../utils/occ";
import { NATENBERG, withBook } from "./bookRefs";
import { requestTicket } from "./ticketIntent";

interface DailyMethodPanelProps {
  account: "paper" | "sim";
  onLoad: (structure: LoadableStructure) => boolean;
  onCloseSpread: (req: CloseSpreadRequest) => Promise<unknown>;
  onSelectSymbol?: (symbol: string) => void;
}

const RULES = withBook(
  "The daily method, run once after the close. Market light: red while SPY is below its 200-day average or its implied volatility runs more than 20 % above its 20-day mean -- no new premium sold then. Entries, richest first: implied rich (IV/RV at least 1.20 or IV rank at least 60 %), no position in the symbol or its group, a bull put spread about 45 days out with the short put near 0.30 delta and the long one about 3 % of spot below, sized so its maximum loss is at most 1 % of equity, all open risk together at most 5 %. Exits: half the credit earned, a loss of twice the credit, or 21 days left. It proposes; nothing is placed until you send the ticket. Backtested 2019-2026 on seven ETFs with synthetic prices at the real daily IV -- positive in both halves, small returns, no skew in the prices.",
  NATENBERG.ivAsPredictor,
  NATENBERG.verticals,
);

const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)} %`);
const money = (v: number) => `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

/** The daily method's proposals: the market light, exits for the bull put
 * spreads the account holds, entries across the watchlist -- backend
 * app/options/daily_method.py. Loading a proposal fills the ticket; the
 * user sends it. */
export function DailyMethodPanel({ account, onLoad, onCloseSpread, onSelectSymbol }: DailyMethodPanelProps) {
  const [result, setResult] = useState<DailyMethodResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState<string | null>(null);
  const [showSkipped, setShowSkipped] = useState(false);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setResult(await runDailyMethod(account));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const close = async (id: string, req: CloseSpreadRequest) => {
    setClosing(id);
    try {
      await onCloseSpread(req);
      await run();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setClosing(null);
    }
  };

  const m = result?.market;
  return (
    <section className="pb-daily">
      <div className="pb-start-head">
        <button type="button" className="generate-button" onClick={() => void run()} disabled={loading} title={RULES}>
          {loading ? "Reading chains…" : "Daily method: check today"}
        </button>
        <span className="order-hint">
          Sell put spreads when premium is rich · market filter · 1 % per trade, 5 % in all ·{" "}
          {account === "paper" ? "paper account" : "simulated account"}
        </span>
      </div>
      {error && <p className="order-rejection">{error}</p>}
      {result && m && (
        <>
          <p className={`pb-daily-light ${m.status}`}>
            <strong>Market {m.status === "green" ? "green" : "red"}</strong>
            {m.reason ? ` — ${m.reason}` : ""} · SPY {m.spy_close?.toFixed(2)} vs 200-day {m.spy_sma200?.toFixed(2) ?? "—"} · SPY
            IV {pct(m.spy_iv, 1)} (limit {pct(m.spy_iv_limit, 1)}) · risk held + proposed {money(result.open_risk)} of{" "}
            {money(result.risk_cap)} · equity {money(result.equity)} · {result.as_of}
          </p>

          {result.exits.length > 0 && (
            <>
              <h4>Exits</h4>
              <ul className="pb-daily-list">
                {result.exits.map((x) => (
                  <li key={x.id}>
                    <strong>{x.symbol}</strong> {x.qty}× {formatExpiry(x.expiry)} {x.strikes[0]}/{x.strikes[1]}P · {x.reason}{" "}
                    <button
                      type="button"
                      className="row-action"
                      disabled={closing === x.id}
                      onClick={() => void close(x.id, { legs: x.close.legs, qty: x.close.qty } as CloseSpreadRequest)}
                      title="Places the close at the current mid, as the Positions tab's close does."
                    >
                      {closing === x.id ? "Closing…" : "Close"}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}

          <h4>Entries</h4>
          {result.entries.length === 0 ? (
            <p className="order-hint">Nothing to open today{m.status === "red" ? " — the market light is red" : ""}.</p>
          ) : (
            <table className="opt-table">
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Spread</th>
                  <th scope="col">Credit</th>
                  <th scope="col">Qty</th>
                  <th scope="col">Max loss</th>
                  <th scope="col">IV/RV</th>
                  <th scope="col">IV rank</th>
                  <th scope="col" aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {result.entries.map((e) => (
                  <tr key={e.symbol}>
                    <th scope="row">
                      <button type="button" className="link-button" onClick={() => onSelectSymbol?.(e.symbol)}>
                        {e.symbol}
                      </button>
                    </th>
                    <td>
                      {formatExpiry(e.expiry)} ({e.dte} d) {e.short_strike}/{e.long_strike}P · Δ {Math.abs(e.short_delta).toFixed(2)}
                    </td>
                    <td title={`Mid ${e.credit_mid.toFixed(2)}, natural ${e.credit_natural.toFixed(2)}. Sized on the natural, the limit set at mid.`}>
                      {e.credit_mid.toFixed(2)}
                    </td>
                    <td>{e.contracts}</td>
                    <td>{money(e.max_loss)}</td>
                    <td>{e.iv_rv == null ? "—" : `${e.iv_rv.toFixed(2)}×`}</td>
                    <td>{e.iv_rank == null ? "—" : `${e.iv_rank.toFixed(0)} %`}</td>
                    <td>
                      <button
                        type="button"
                        className="row-action"
                        onClick={() => {
                          const structure: LoadableStructure = { strategy: "bull_put", ticket: e.ticket as unknown as SpreadTicketRequest };
                          // onLoad only plants a ticket on the symbol the widget
                          // already shows; for another one, switch to it and hand
                          // the ticket over once its chain is in -- the Screener's
                          // way (ticketIntent).
                          if (!onLoad(structure)) {
                            onSelectSymbol?.(e.symbol);
                            requestTicket({ symbol: e.symbol, structure });
                          }
                        }}
                        title="Fills the ticket with this spread at the mid. Nothing is sent until you place it."
                      >
                        Load
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {result.skipped.length > 0 && (
            <p className="order-hint">
              <button type="button" className="link-button" onClick={() => setShowSkipped((v) => !v)}>
                {showSkipped ? "Hide" : "Show"} {result.skipped.length} not opened
              </button>
            </p>
          )}
          {showSkipped && (
            <ul className="pb-daily-list">
              {result.skipped.map((s, i) => (
                <li key={`${s.symbol}-${i}`}>
                  <strong>{s.symbol}</strong> — {s.reason}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
