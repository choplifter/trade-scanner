import { useEffect, useRef, useState } from "react";

import { getTradeIdeasPerformance, postTradeIdeas } from "../../api/http";
import type { TradeIdea, TradeIdeaPerformance } from "../../types/tradeIdeas";
import { formatPrice, tradingViewSymbol } from "../../utils/format";
import { CopyButton } from "../common/CopyButton";

const PERFORMANCE_POLL_MS = 30_000;

interface TradeIdeasWidgetProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

export function TradeIdeasWidget({ selectedSymbol, onSelectSymbol }: TradeIdeasWidgetProps) {
  const [ideas, setIdeas] = useState<TradeIdea[]>([]);
  const [disclaimer, setDisclaimer] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [performance, setPerformance] = useState<TradeIdeaPerformance[]>([]);

  const refreshPerformance = () => {
    getTradeIdeasPerformance()
      .then((res) => setPerformance(res.picks))
      .catch(() => {
        // Best-effort background refresh -- a failed poll shouldn't disrupt
        // the widget or overwrite the last-known performance table.
      });
  };

  // Past-picks performance is backend state (survives across page loads
  // within a backend run), so it's worth showing on mount, not just after a
  // fresh Generate click -- and it's worth polling since prices move.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    refreshPerformance();
    pollRef.current = setInterval(refreshPerformance, PERFORMANCE_POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const generate = () => {
    setLoading(true);
    setError(null);
    postTradeIdeas()
      .then((res) => {
        setIdeas(res.ideas);
        setDisclaimer(res.disclaimer);
        refreshPerformance();
      })
      .catch((err: unknown) => setError(String(err instanceof Error ? err.message : err)))
      .finally(() => setLoading(false));
  };

  return (
    <div className="widget ai-ideas-widget">
      <div className="widget-header">
        <h2>AI Top 3 Trade Ideas</h2>
        <button className="generate-button" onClick={generate} disabled={loading}>
          {loading ? "Generating…" : "Generate"}
        </button>
      </div>
      <div className="widget-body ai-ideas-body">
        {error ? (
          <div className="widget-error">{error}</div>
        ) : ideas.length === 0 ? (
          <div className="widget-empty">
            {loading
              ? "Asking Claude to pick today's top 3 setups…"
              : "Click Generate to have Claude select today's 3 most notable setups from the gainers scanner — every candidate already requires a genuine, stock-specific news catalyst, ranked first by how significant that news is, then by gap %, relative volume, dollar volume, high-of-day status, VWAP, momentum, spread, multi-day context, float, and short interest."}
          </div>
        ) : (
          <>
            <ol className="ai-ideas-list">
              {ideas.map((idea, i) => (
                <li
                  key={idea.symbol}
                  aria-selected={idea.symbol === selectedSymbol}
                  onClick={() => onSelectSymbol(idea.symbol)}
                >
                  <span className="ai-idea-rank">#{i + 1}</span>
                  <span className="ai-idea-symbol">{idea.symbol}</span>
                  <CopyButton
                    value={tradingViewSymbol(idea.symbol, idea.exchange)}
                    title={`Copy "${tradingViewSymbol(idea.symbol, idea.exchange)}" to clipboard (for TradingView search)`}
                  />
                  <span
                    className="ai-idea-score"
                    title="How many scanner signals (catalyst, VWAP, momentum, spread, continuation, range) line up for this pick, not a prediction of outcome"
                  >
                    Signal {idea.signal_score}/10
                  </span>
                  <span className="ai-idea-headline">{idea.headline}</span>
                  <p className="ai-idea-note">{idea.reason}</p>
                </li>
              ))}
            </ol>
            {disclaimer && <p className="ai-ideas-disclaimer">{disclaimer}</p>}
          </>
        )}

        {performance.length > 0 && (
          <div className="ai-ideas-performance">
            <h3>Past picks since generated</h3>
            <table className="performance-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Picked</th>
                  <th>Entry</th>
                  <th>Now</th>
                  <th>Change</th>
                </tr>
              </thead>
              <tbody>
                {performance.map((p, i) => (
                  <tr
                    key={`${p.symbol}-${p.generated_at}-${i}`}
                    aria-selected={p.symbol === selectedSymbol}
                    onClick={() => onSelectSymbol(p.symbol)}
                  >
                    <td className="symbol-cell">{p.symbol}</td>
                    <td>{p.minutes_since < 1 ? "just now" : `${Math.round(p.minutes_since)}m ago`}</td>
                    <td>{formatPrice(p.entry_price)}</td>
                    <td>{p.current_price != null ? formatPrice(p.current_price) : "—"}</td>
                    <td className={pctClass(p.pct_change_since_pick)}>
                      {p.pct_change_since_pick != null ? `${p.pct_change_since_pick > 0 ? "+" : ""}${p.pct_change_since_pick.toFixed(2)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="ai-ideas-disclaimer">
              How past AI-generated picks have moved since they were selected -- a self-check on the
              ranking, not a track record to trade on.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function pctClass(pct: number | null): string {
  if (pct == null || pct === 0) return "";
  return pct > 0 ? "delta-up" : "delta-down";
}
