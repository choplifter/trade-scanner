import { useEffect, useRef, useState } from "react";

import { getScannerBenchmarkPerformance } from "../../api/http";
import type { ScannerBenchmarkPick } from "../../types/scannerBenchmark";
import { formatPrice, formatRvol } from "../../utils/format";

const POLL_MS = 30_000;

const VIEW_LABEL: Record<string, string> = {
  gainers: "Gainers",
  losers: "Losers",
  most_active: "Most Active",
};

function pctText(pct: number | null): string {
  if (pct == null) return "—";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function pctClass(pct: number | null): string {
  if (pct == null || pct === 0) return "";
  return pct > 0 ? "delta-up" : "delta-down";
}

/** Whether the symbols the *scanner itself* first flags (gap %/RVOL/movers
 * backstop -- every ranked view, not just the 3 AI picks TradeIdeasWidget
 * tracks) actually keep moving afterward, versus the broad market. The
 * real self-check on the scanner's own selection criteria. */
export function ScannerBenchmarkWidget() {
  const [picks, setPicks] = useState<ScannerBenchmarkPick[]>([]);
  const [benchmarkSymbol, setBenchmarkSymbol] = useState("SPY");
  const [error, setError] = useState<string | null>(null);

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
            Waiting for the scanner to flag its first symbol -- every gainer, loser, or most-active
            entry gets logged here the moment it first appears, then tracked against {benchmarkSymbol}
            from that instant.
          </div>
        ) : (
          <>
            <table className="performance-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Via</th>
                  <th>Flagged</th>
                  <th>Entry Gap%</th>
                  <th>Entry RVol</th>
                  <th>Entry $</th>
                  <th>Now $</th>
                  <th>Change</th>
                  <th>{benchmarkSymbol}</th>
                  <th>Alpha</th>
                </tr>
              </thead>
              <tbody>
                {picks.map((p) => (
                  <tr key={p.symbol}>
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
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="ai-ideas-disclaimer">
              Every symbol the scanner itself has flagged (gainers/losers/most active), tracked from
              the moment it first appeared -- a self-check on the scanner's own selection criteria, not
              a trading record. "Just now" entries haven't had time to move yet.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
