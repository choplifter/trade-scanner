import { useEffect, useMemo, useRef, useState } from "react";

import { getScannerBenchmarkPerformance } from "../../api/http";
import type { ScannerBenchmarkPick } from "../../types/scannerBenchmark";
import { formatPrice, formatRvol } from "../../utils/format";

const POLL_MS = 30_000;

const VIEW_LABEL: Record<string, string> = {
  gainers: "Gainers",
  losers: "Losers",
  most_active: "Most Active",
  moderate_movers: "Moderate 3-8%",
};

function pctText(pct: number | null): string {
  if (pct == null) return "—";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function pctClass(pct: number | null): string {
  if (pct == null || pct === 0) return "";
  return pct > 0 ? "delta-up" : "delta-down";
}

type SortColumn =
  | "symbol"
  | "view"
  | "minutes_since"
  | "entry_pct_change"
  | "entry_rvol"
  | "entry_price"
  | "current_price"
  | "pct_change_since_entry"
  | "benchmark_pct_change_since_entry"
  | "alpha_vs_benchmark";

type SortDirection = "asc" | "desc";

// Sorts the raw numeric/string values, not the formatted display text --
// same lexicographic-vs-numeric mismatch the Dash tables' custom sort
// avoids (e.g. "-8.66%" vs "-22.00%" as strings).
const SORT_KEYS: Record<SortColumn, (p: ScannerBenchmarkPick) => number | string | null> = {
  symbol: (p) => p.symbol,
  view: (p) => p.view,
  minutes_since: (p) => p.minutes_since,
  entry_pct_change: (p) => p.entry_pct_change,
  entry_rvol: (p) => p.entry_rvol,
  entry_price: (p) => p.entry_price,
  current_price: (p) => p.current_price,
  pct_change_since_entry: (p) => p.pct_change_since_entry,
  benchmark_pct_change_since_entry: (p) => p.benchmark_pct_change_since_entry,
  alpha_vs_benchmark: (p) => p.alpha_vs_benchmark,
};

function sortPicks(picks: ScannerBenchmarkPick[], column: SortColumn, direction: SortDirection): ScannerBenchmarkPick[] {
  const keyFn = SORT_KEYS[column];
  const withValue = picks.filter((p) => keyFn(p) !== null);
  const withoutValue = picks.filter((p) => keyFn(p) === null);
  withValue.sort((a, b) => {
    const av = keyFn(a) as number | string;
    const bv = keyFn(b) as number | string;
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return direction === "asc" ? cmp : -cmp;
  });
  // Missing values always sort last, regardless of direction -- matches
  // the Dash tables' custom sort (a symbol with no live price yet isn't
  // "lowest", it's unknown).
  return [...withValue, ...withoutValue];
}

interface ScannerBenchmarkWidgetProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

/** Whether the symbols the *scanner itself* first flags (gap %/RVOL/movers
 * backstop -- every ranked view, not just the 3 AI picks TradeIdeasWidget
 * tracks) actually keep moving afterward, versus the broad market. The
 * real self-check on the scanner's own selection criteria. */
export function ScannerBenchmarkWidget({ selectedSymbol, onSelectSymbol }: ScannerBenchmarkWidgetProps) {
  const [picks, setPicks] = useState<ScannerBenchmarkPick[]>([]);
  const [benchmarkSymbol, setBenchmarkSymbol] = useState("SPY");
  const [error, setError] = useState<string | null>(null);
  const [sortColumn, setSortColumn] = useState<SortColumn | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    const refresh = () => {
      getScannerBenchmarkPerformance()
        .then((res) => {
          setPicks(res.picks);
          setBenchmarkSymbol(res.benchmark_symbol);
          setError(null);
        })
        .catch((err: unknown) => setError(String(err instanceof Error ? err.message : err)));
    };
    refresh();
    pollRef.current = setInterval(refresh, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleSort = (column: SortColumn) => {
    if (column === sortColumn) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("desc");
    }
  };

  const sortedPicks = useMemo(
    () => (sortColumn ? sortPicks(picks, sortColumn, sortDirection) : picks),
    [picks, sortColumn, sortDirection],
  );

  const headerProps = (column: SortColumn) => ({
    className: "sortable-header",
    "aria-sort": (sortColumn === column ? (sortDirection === "asc" ? "ascending" : "descending") : "none") as
      | "ascending"
      | "descending"
      | "none",
    onClick: () => handleSort(column),
  });

  const sortIndicator = (column: SortColumn) =>
    sortColumn === column ? (sortDirection === "asc" ? " ▲" : " ▼") : "";

  return (
    <div className="widget scanner-benchmark-widget">
      <div className="widget-header">
        <h2>Scanner vs {benchmarkSymbol}</h2>
        <span className="widget-count">{picks.length}</span>
      </div>
      <div className="widget-body">
        {error ? (
          <div className="widget-error">{error}</div>
        ) : picks.length === 0 ? (
          <div className="widget-empty">
            Waiting for the scanner to flag its first symbol -- every gainer, loser, most-active or
            moderate-mover entry gets logged here the moment it first appears, then tracked against{" "}
            {benchmarkSymbol} from that instant.
          </div>
        ) : (
          <>
            <table className="performance-table">
              <thead>
                <tr>
                  <th {...headerProps("symbol")}>Symbol{sortIndicator("symbol")}</th>
                  <th {...headerProps("view")}>Via{sortIndicator("view")}</th>
                  <th {...headerProps("minutes_since")}>Flagged{sortIndicator("minutes_since")}</th>
                  <th {...headerProps("entry_pct_change")}>Entry Gap%{sortIndicator("entry_pct_change")}</th>
                  <th {...headerProps("entry_rvol")}>Entry RVol{sortIndicator("entry_rvol")}</th>
                  <th {...headerProps("entry_price")}>Entry ${sortIndicator("entry_price")}</th>
                  <th {...headerProps("current_price")}>Now ${sortIndicator("current_price")}</th>
                  <th {...headerProps("pct_change_since_entry")}>Change{sortIndicator("pct_change_since_entry")}</th>
                  <th {...headerProps("benchmark_pct_change_since_entry")}>
                    {benchmarkSymbol}
                    {sortIndicator("benchmark_pct_change_since_entry")}
                  </th>
                  <th {...headerProps("alpha_vs_benchmark")}>Alpha{sortIndicator("alpha_vs_benchmark")}</th>
                  <th>News</th>
                </tr>
              </thead>
              <tbody>
                {sortedPicks.map((p) => (
                  <tr
                    // Symbol alone is not unique: the tracker keys entries per
                    // (symbol, view), so a stock in gainers and moderate_movers
                    // is legitimately two rows. A duplicate React key here made
                    // rows silently fail to render.
                    key={`${p.symbol}-${p.view}`}
                    aria-selected={p.symbol === selectedSymbol}
                    onClick={() => onSelectSymbol(p.symbol)}
                  >
                    <td className="symbol-cell">{p.symbol}</td>
                    <td>{VIEW_LABEL[p.view] ?? p.view}</td>
                    <td>{p.minutes_since < 1 ? "just now" : `${Math.round(p.minutes_since)}m ago`}</td>
                    <td className={pctClass(p.entry_pct_change)}>{pctText(p.entry_pct_change)}</td>
                    <td>{formatRvol(p.entry_rvol)}</td>
                    <td>{formatPrice(p.entry_price)}</td>
                    <td>{p.current_price != null ? formatPrice(p.current_price) : "—"}</td>
                    <td className={pctClass(p.pct_change_since_entry)}>
                      {pctText(p.pct_change_since_entry)}
                    </td>
                    <td className={pctClass(p.benchmark_pct_change_since_entry)}>
                      {pctText(p.benchmark_pct_change_since_entry)}
                    </td>
                    <td
                      className={pctClass(p.alpha_vs_benchmark)}
                      title="This symbol's move since being flagged, minus the benchmark's move over the same window"
                    >
                      {pctText(p.alpha_vs_benchmark)}
                    </td>
                    <td
                      className="news-cell"
                      title={p.entry_headline ?? p.current_headline ?? undefined}
                    >
                      {/* Prefer the headline captured at entry; fall back to
                          today's. Most entries are first flagged premarket
                          with no news yet, so entry_headline alone reads as
                          "no news" for symbols that plainly have some. */}
                      {p.entry_headline ?? p.current_headline ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="ai-ideas-disclaimer">
              Every symbol the scanner itself has flagged (gainers/losers/most active/moderate 3-8%), tracked from
              the moment it first appeared -- a self-check on the scanner's own selection criteria, not
              a trading record. "Just now" entries haven't had time to move yet.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
