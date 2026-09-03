import { useEffect, useRef, useState } from "react";

import { getScannerHistoryPerformance } from "../../api/http";
import type {
  ScannerHistoryBucketRow,
  ScannerHistoryHorizon,
  ScannerHistoryPick,
  ScannerHistoryResponse,
} from "../../types/scannerHistory";
import { formatRvol } from "../../utils/format";
import { symbolDragProps } from "../../utils/dragSymbol";

const POLL_MS = 60_000;

const VIEW_LABEL: Record<string, string> = {
  gainers: "Gainers",
  losers: "Losers",
  most_active: "Most Active",
  moderate_movers: "Moderate 3-8%",
};

const HORIZONS: { id: ScannerHistoryHorizon; label: string }[] = [
  { id: "30m", label: "30m" },
  { id: "60m", label: "60m" },
  { id: "latest", label: "Latest" },
];

function pctText(pct: number | null): string {
  if (pct == null) return "—";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function pctClass(pct: number | null): string {
  if (pct == null || pct === 0) return "";
  return pct > 0 ? "delta-up" : "delta-down";
}

interface LeaderboardTableProps {
  title: string;
  picks: ScannerHistoryPick[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

function LeaderboardTable({ title, picks, selectedSymbol, onSelectSymbol }: LeaderboardTableProps) {
  return (
    <div className="ai-ideas-performance">
      <h3>{title}</h3>
      {picks.length === 0 ? (
        <div className="widget-empty">Not enough data yet.</div>
      ) : (
        <table className="performance-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Via</th>
              <th>Date</th>
              <th>Entry Gap%</th>
              <th>Entry RVol</th>
              <th>Change</th>
              <th>Alpha</th>
              <th>News</th>
            </tr>
          </thead>
          <tbody>
            {picks.map((p) => (
              <tr
                key={`${p.symbol}-${p.view}-${p.trading_date}`}
                aria-selected={p.symbol === selectedSymbol}
                onClick={() => onSelectSymbol(p.symbol)}
              >
                <td className="symbol-cell" {...symbolDragProps(p.symbol)}>
                  {p.symbol}
                </td>
                <td>{VIEW_LABEL[p.view] ?? p.view}</td>
                <td>{p.trading_date}</td>
                <td className={pctClass(p.entry_pct_change)}>{pctText(p.entry_pct_change)}</td>
                <td>{formatRvol(p.entry_rvol)}</td>
                <td className={pctClass(p.pct_change_since_entry)}>
                  {pctText(p.pct_change_since_entry)}
                </td>
                <td
                  className={pctClass(p.alpha_vs_benchmark)}
                  title="This symbol's move since being flagged, minus SPY's move over the same window"
                >
                  {pctText(p.alpha_vs_benchmark)}
                </td>
                <td className="news-cell" title={p.entry_headline ?? undefined}>
                  {p.entry_headline ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function BucketTable({ title, rows }: { title: string; rows: ScannerHistoryBucketRow[] }) {
  return (
    <div className="ai-ideas-performance">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <div className="widget-empty">Not enough data yet.</div>
      ) : (
        <table className="performance-table">
          <thead>
            <tr>
              <th>View</th>
              <th>Bucket</th>
              <th>Sample</th>
              <th>Win Rate</th>
              <th>Avg Return</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.view}-${r.bucket}`}>
                <td>{VIEW_LABEL[r.view] ?? r.view}</td>
                <td>{r.bucket}</td>
                <td>{r.sample_size}</td>
                <td>{r.win_rate.toFixed(1)}%</td>
                <td className={pctClass(r.avg_return)}>{pctText(r.avg_return)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface ScannerHistoryWidgetProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

/** Aggregated, persisted (SQLite-backed) view of how every scanner match
 * has performed since being flagged, across restarts and days -- unlike
 * ScannerBenchmarkWidget, which only sees whatever's still resident in this
 * process's in-memory tracker since it last restarted. */
export function ScannerHistoryWidget({ selectedSymbol, onSelectSymbol }: ScannerHistoryWidgetProps) {
  const [data, setData] = useState<ScannerHistoryResponse | null>(null);
  const [horizon, setHorizon] = useState<ScannerHistoryHorizon>("latest");
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    const refresh = () => {
      getScannerHistoryPerformance()
        .then((res) => {
          setData(res);
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

  const summaryForHorizon = (data?.summary ?? []).filter((s) => s.horizon === horizon);
  const totalSample = summaryForHorizon.reduce((sum, s) => sum + s.sample_size, 0);

  return (
    <div className="widget scanner-history-widget">
      <div className="widget-header">
        <h2>Scanner Match History</h2>
        <span className="widget-count">{totalSample}</span>
      </div>
      <div className="widget-body">
        {error ? (
          <div className="widget-error">{error}</div>
        ) : !data || data.summary.length === 0 ? (
          <div className="widget-empty">
            No scanner matches logged yet -- every gainer, loser, or most-active entry gets
            recorded to the persistent history the moment it first appears each trading day, then
            checked periodically afterward. Give it a few sessions to build up a sample.
          </div>
        ) : (
          <>
            <div className="timeframe-selector">
              {HORIZONS.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  className="timeframe-button"
                  aria-pressed={h.id === horizon}
                  onClick={() => setHorizon(h.id)}
                >
                  {h.label}
                </button>
              ))}
            </div>
            <table className="performance-table scanner-history-summary">
              <thead>
                <tr>
                  <th>View</th>
                  <th>Sample</th>
                  <th>Win Rate</th>
                  <th>Avg Return</th>
                  <th>Avg Alpha</th>
                </tr>
              </thead>
              <tbody>
                {summaryForHorizon.map((s) => (
                  <tr key={s.view}>
                    <td>{VIEW_LABEL[s.view] ?? s.view}</td>
                    <td>{s.sample_size}</td>
                    <td>{s.win_rate != null ? `${s.win_rate.toFixed(1)}%` : "—"}</td>
                    <td className={pctClass(s.avg_return)}>{pctText(s.avg_return)}</td>
                    <td className={pctClass(s.avg_alpha)}>{pctText(s.avg_alpha)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <LeaderboardTable
              title="Best matches"
              picks={data.leaderboard_best}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={onSelectSymbol}
            />
            <LeaderboardTable
              title="Worst matches"
              picks={data.leaderboard_worst}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={onSelectSymbol}
            />

            <BucketTable
              title="Fade risk — by gap size"
              rows={data.gap_buckets.filter((b) => b.horizon === horizon)}
            />
            <BucketTable
              title="Fade risk — by entry RVOL"
              rows={data.rvol_buckets.filter((b) => b.horizon === horizon)}
            />

            <p className="ai-ideas-disclaimer">
              Persisted to SQLite, so this survives backend restarts -- covers every scanner match
              from the last 7 trading days. Leaderboards are always ranked by the most recent
              price check for each match, regardless of the horizon selected above. Fade-risk
              tables check whether bigger gaps / higher RVOL predict worse subsequent performance
              ("gap and crap") and now follow that selector too -- on "Latest" they mix holding
              periods, minutes for a fresh match and days for an old one, so a bucket can look
              strong there and flat at a fixed horizon. Read the small-sample buckets skeptically
              until more days build up.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
