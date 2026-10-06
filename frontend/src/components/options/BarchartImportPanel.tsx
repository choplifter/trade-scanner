import { useMemo, useState } from "react";

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

// How many rows to check against our chains, and how many chains that may
// cost: rows sharing a (symbol, expiry) share a chain, so rows run cheap.
const DEPTHS = [
  { rows: 40, chains: 40, label: "first 40 rows" },
  { rows: 200, chains: 60, label: "first 200 rows" },
  { rows: 1000, chains: 100, label: "first 1000 rows (slow)" },
];

type SortKey = "Symbol" | "Expiry" | "Net" | "Loss BC" | "Ours skew" | "EV" | "EV (RV)";
const SORTS: Record<SortKey, (r: BarchartScreenRow) => number | string | null> = {
  Symbol: (r) => r.symbol,
  Expiry: (r) => r.dte,
  Net: (r) => r.net,
  "Loss BC": (r) => r.loss_prob,
  "Ours skew": (r) => r.ours?.loss_prob_skew ?? null,
  EV: (r) => r.ours?.expected_value ?? null,
  "EV (RV)": (r) => r.ours?.expected_value_rv ?? null,
};

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
  const [depth, setDepth] = useState(0);
  // Best expectation at realised vol first by default -- the figure a screen
  // for implied over realised is after; rows without a figure sink.
  const [sort, setSort] = useState<{ by: SortKey; desc: boolean }>({ by: "EV (RV)", desc: true });
  const onSort = (by: SortKey) => setSort((s) => (s.by === by ? { by, desc: !s.desc } : { by, desc: by !== "Symbol" }));
  const sorted = useMemo(() => {
    if (!result) return [];
    const get = SORTS[sort.by];
    return [...result.rows].sort((a, b) => {
      const x = get(a);
      const y = get(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      const cmp = typeof x === "string" ? x.localeCompare(String(y)) : (x as number) - (y as number);
      return sort.desc ? -cmp : cmp;
    });
  }, [result, sort]);
  const th = (label: SortKey, title?: string) => (
    <th scope="col" title={title} aria-sort={sort.by === label ? (sort.desc ? "descending" : "ascending") : "none"}>
      <button type="button" className="link-button" onClick={() => onSort(label)}>
        {label}
        {sort.by === label ? (sort.desc ? " ▼" : " ▲") : ""}
      </button>
    </th>
  );

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
      setResult(await importBarchartScreen(chosen, DEPTHS[depth].rows, DEPTHS[depth].chains));
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
            <select value={depth} onChange={(e) => setDepth(Number(e.target.value))} title="Rows checked against today's chains. Rows on the same symbol and expiry share one chain fetch.">
              {DEPTHS.map((d, i) => (
                <option key={d.rows} value={i}>
                  check {d.label}
                </option>
              ))}
            </select>
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
              {result.stale && (
                <p className="pb-daily-warning">
                  This export is from {result.as_of ? formatExpiry(result.as_of) : "an earlier day"}: its prices (Net, Max P / L,
                  Loss BC) are that day's. Our columns are priced at today's quotes for the same strikes — compare those, and
                  download a fresh export for Barchart's side.
                </p>
              )}
              <p className="order-hint">
                {result.file}: {result.rows_total} rows, the first {result.checked} checked against today's chains
                {result.rows.length < result.rows_total ? `, ${result.rows.length} shown` : ""}.
                {result.problems.length > 0 ? ` ${result.problems.length} lines skipped.` : ""}
              </p>
              <table className="opt-table bc-import-table">
                <thead>
                  <tr>
                    {th("Symbol")}
                    {th("Expiry")}
                    <th scope="col">Legs</th>
                    {th("Net", "Per share at Barchart's natural prices; positive is a credit.")}
                    <th scope="col" title="Per contract, from Barchart's per-share figures × 100.">Max P / L</th>
                    <th scope="col" title="Today's natural for the same strikes: sold at the bid, bought at the ask. Our loss probabilities and EV are priced at it.">
                      Net now
                    </th>
                    {th("Loss BC", SKEW_TITLE)}
                    <th scope="col" title={SKEW_TITLE}>Ours flat</th>
                    {th("Ours skew", SKEW_TITLE)}
                    {th("EV", "Our expected value per contract at today's natural prices for the same strikes, on the at-the-money distribution. About zero for anything priced at its own IV.")}
                    {th("EV (RV)", "The same at the stock's realised-vol forecast instead of its IV: what the condor earns on average if the stock moves as its history says. Positive where implied runs enough above realised to pay for the risk and the bid/ask.")}
                    <th scope="col">Earnings</th>
                    <th scope="col" title="Barchart's IV rank / ours (ours needs 20 recorded sessions).">IV rank</th>
                    <th scope="col" aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r, i) => {
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
                        <td title={o?.priced_at === "file" ? "A leg is not quoted today: our figures use the file's net" : undefined}>
                          {o?.net_now == null ? "—" : o.net_now.toFixed(2)}
                        </td>
                        <td>{pct(r.loss_prob)}</td>
                        <td>{o?.note ? <span title={o.note}>…</span> : pct(o?.loss_prob_flat)}</td>
                        <td className={worse ? "delta-down" : undefined}>{pct(o?.loss_prob_skew)}</td>
                        <td className={o?.expected_value == null ? undefined : o.expected_value > 0 ? "delta-up" : "delta-down"}>
                          {o?.expected_value == null ? "—" : o.expected_value.toFixed(0)}
                        </td>
                        <td
                          className={o?.expected_value_rv == null ? undefined : o.expected_value_rv > 0 ? "delta-up" : "delta-down"}
                          title={o?.rv_forecast != null ? `Realised-vol forecast ${(o.rv_forecast * 100).toFixed(1)} %` : undefined}
                        >
                          {o?.expected_value_rv == null ? "—" : o.expected_value_rv.toFixed(0)}
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
