import { useState } from "react";

import { importBarchartScreen, listBarchartScreens } from "../../api/options";
import type { BarchartImportResponse, BarchartScreenFile, BarchartScreenRow, LoadableStructure } from "../../types/options";
import { formatExpiry } from "../../utils/occ";
import { NATENBERG, withBook } from "./bookRefs";
import { requestTicket } from "./ticketIntent";

interface BarchartImportPanelProps {
  onSelectSymbol?: (symbol: string) => void;
}

const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)} %`);

const SKEW_TITLE = withBook(
  "Barchart's loss probability rests on the at-the-money lognormal, as did ours before the skew (checked on 784 of its condors to about a point). 'Ours flat' is that same model on today's chain; 'ours skew' reads the distribution the chain's strikes price. Where skew is higher, the market prices the losing side as likelier than Barchart's figure says.",
  NATENBERG.impliedDistributions,
);

function strikes(row: BarchartScreenRow): string {
  return row.legs
    .map((l) => `${l.side === "sell" ? "-" : "+"}${l.strike}${l.kind === "put" ? "P" : "C"}`)
    .join(" ");
}

/** Barchart option-screener exports from the Downloads folder, set beside
 * our own numbers for the same structures -- backend
 * app/options/barchart_screener.py. A Ticket button loads a row's spread. */
export function BarchartImportPanel({ onSelectSymbol }: BarchartImportPanelProps) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<BarchartScreenFile[] | null>(null);
  const [directory, setDirectory] = useState<string>("");
  const [chosen, setChosen] = useState<string>("");
  const [result, setResult] = useState<BarchartImportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setError(null);
    try {
      const res = await listBarchartScreens();
      setFiles(res.files);
      setDirectory(res.directory);
      setChosen((c) => (c && res.files.some((f) => f.name === c) ? c : (res.files[0]?.name ?? "")));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) void refresh();
  };

  const runImport = async () => {
    if (!chosen) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await importBarchartScreen(chosen));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const loadTicket = (row: BarchartScreenRow) => {
    if (!row.ticket || !row.strategy) return;
    const structure: LoadableStructure = { strategy: row.strategy, ticket: row.ticket };
    onSelectSymbol?.(row.symbol);
    requestTicket({ symbol: row.symbol, structure });
  };

  return (
    <section className="bc-import">
      <button type="button" className="timeframe-button" aria-expanded={open} onClick={toggle}>
        Barchart import
      </button>
      {open && (
        <>
          <div className="bc-import-controls">
            {files && files.length === 0 ? (
              <span className="order-hint">No Barchart screener exports in {directory}.</span>
            ) : (
              <select value={chosen} onChange={(e) => setChosen(e.target.value)} disabled={!files}>
                {(files ?? []).map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name} · {new Date(f.modified * 1000).toLocaleString()}
                  </option>
                ))}
              </select>
            )}
            <button type="button" className="generate-button" onClick={() => void runImport()} disabled={loading || !chosen}>
              {loading ? "Reading chains…" : "Import"}
            </button>
            <button type="button" className="link-button" onClick={() => void refresh()}>
              refresh list
            </button>
          </div>
          {error && <p className="order-rejection">{error}</p>}
          {result && (
            <>
              <p className="order-hint">
                {result.file}: {result.rows_total} rows, the first {result.checked} checked against today's chains
                {result.rows.length < result.rows_total ? `, ${result.rows.length} shown` : ""}.
                {result.problems.length > 0 ? ` ${result.problems.length} lines skipped.` : ""}
              </p>
              <table className="opt-table bc-import-table">
                <thead>
                  <tr>
                    <th scope="col">Symbol</th>
                    <th scope="col">Expiry</th>
                    <th scope="col">Legs</th>
                    <th scope="col" title="Per share at Barchart's natural prices; positive is a credit.">Net</th>
                    <th scope="col" title="Per contract, from Barchart's per-share figures × 100.">Max P / L</th>
                    <th scope="col" title={SKEW_TITLE}>Loss BC</th>
                    <th scope="col" title={SKEW_TITLE}>Ours flat</th>
                    <th scope="col" title={SKEW_TITLE}>Ours skew</th>
                    <th scope="col" title="Our expected value per contract at Barchart's prices, on the at-the-money distribution.">EV</th>
                    <th scope="col">Earnings</th>
                    <th scope="col" title="Barchart's IV rank / ours (ours needs 20 recorded sessions).">IV rank</th>
                    <th scope="col" aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r, i) => {
                    const o = r.ours;
                    const worse = o?.loss_prob_skew != null && r.loss_prob != null && o.loss_prob_skew > r.loss_prob + 0.02;
                    return (
                      <tr key={`${r.symbol}-${r.expiry}-${i}`}>
                        <th scope="row">{r.symbol}</th>
                        <td>
                          {formatExpiry(r.expiry)}
                          {r.dte != null ? ` (${r.dte} d)` : ""}
                        </td>
                        <td>{strikes(r)}</td>
                        <td>{r.net == null ? "—" : r.net.toFixed(2)}</td>
                        <td>
                          {r.max_profit == null ? "—" : `$${(r.max_profit * 100).toFixed(0)}`} /{" "}
                          {r.max_loss == null ? "—" : `$${(r.max_loss * 100).toFixed(0)}`}
                        </td>
                        <td>{pct(r.loss_prob)}</td>
                        <td>{o?.note ? <span title={o.note}>…</span> : pct(o?.loss_prob_flat)}</td>
                        <td className={worse ? "delta-down" : undefined}>{pct(o?.loss_prob_skew)}</td>
                        <td className={o?.expected_value == null ? undefined : o.expected_value > 0 ? "delta-up" : "delta-down"}>
                          {o?.expected_value == null ? "—" : o.expected_value.toFixed(0)}
                        </td>
                        <td className={o?.earnings_inside ? "delta-down" : undefined} title={o?.earnings_inside ? "Reports before this expiry" : undefined}>
                          {o?.earnings_date ? formatExpiry(o.earnings_date) : "—"}
                        </td>
                        <td>
                          {r.iv_rank == null ? "—" : `${r.iv_rank.toFixed(0)} %`} / {o?.iv_rank == null ? "—" : `${o.iv_rank.toFixed(0)} %`}
                        </td>
                        <td>
                          {r.ticket && (
                            <button type="button" className="row-action" onClick={() => loadTicket(r)} title="Load this structure into the spread ticket">
                              Ticket
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </section>
  );
}
