import { useEffect, useRef } from "react";

import type { SymbolInfoState } from "../../hooks/useSymbolInfo";

interface SymbolInfoPanelProps {
  symbol: string | null;
  /** Fetched by ChartWidget rather than here: the chart needs the same
   * news (as timeline markers), and two callers of the hook would mean
   * two fetches of the same payload. */
  state: SymbolInfoState;
  /** Publish times (unix seconds) of stories whose chart pin was clicked
   * -- those items are highlighted and the first is scrolled into view. */
  highlightTimes?: number[] | null;
}

function publishTime(publishedAt: string): number {
  return Math.floor(Date.parse(publishedAt) / 1000);
}

/** Relative age of a story. The panel is headed "Recent News" but the FMP
 * feed carries a symbol's whole history, so an item can easily be weeks old
 * — without a date, a stale headline reads as an explanation for today's
 * move. Shown per item rather than filtering them out: older context is
 * still useful here, it just must not masquerade as fresh. */
function newsAge(publishedAt: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(publishedAt).getTime()) / 60000));
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function SymbolInfoPanel({ symbol, state, highlightTimes }: SymbolInfoPanelProps) {
  const { info, loading, error } = state;
  const highlighted = new Set(highlightTimes ?? []);
  const firstHighlightRef = useRef<HTMLDivElement | null>(null);

  // On a new click (new array identity), bring the story into view --
  // the panel sits below the chart and is usually scrolled elsewhere.
  useEffect(() => {
    firstHighlightRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [highlightTimes]);

  if (!symbol) return null;
  if (error) return null; // ChartWidget's own error state already covers this symbol
  if (loading && !info) {
    return <div className="symbol-info-panel widget-empty">Loading company info…</div>;
  }
  if (!info) return null;

  const headerBits = [info.company_name, info.sector, info.industry].filter(Boolean);
  const hasProfile = headerBits.length > 0 || !!info.description;

  return (
    <div className="symbol-info-panel">
      {hasProfile && (
        <div className="symbol-info-header">
          {headerBits.length > 0 && <span className="symbol-info-tags">{headerBits.join(" · ")}</span>}
          {info.description && <p className="symbol-info-description">{info.description}</p>}
          {info.website && (
            <a href={info.website} target="_blank" rel="noreferrer" className="symbol-info-website">
              {info.website}
            </a>
          )}
        </div>
      )}

      {info.news.length > 0 ? (
        <div className="symbol-news-list">
          <h3 className="symbol-news-title">Recent News</h3>
          {info.news.map((item, i) => {
            const isHighlighted = highlighted.has(publishTime(item.published_at));
            const firstHighlightedIndex = info.news.findIndex((n) =>
              highlighted.has(publishTime(n.published_at)),
            );
            return (
            <div
              className={isHighlighted ? "symbol-news-item highlighted" : "symbol-news-item"}
              key={i}
              ref={i === firstHighlightedIndex ? firstHighlightRef : undefined}
            >
              {item.url ? (
                <a href={item.url} target="_blank" rel="noreferrer" className="symbol-news-headline">
                  {item.headline}
                </a>
              ) : (
                <span className="symbol-news-headline">{item.headline}</span>
              )}
              <span className="symbol-news-source">
                {" "}
                — {item.source} · {newsAge(item.published_at)}
              </span>
            </div>
            );
          })}
        </div>
      ) : hasProfile ? (
        // Says so explicitly rather than rendering nothing. The news list is
        // now bounded to the last session, so an empty one is a real answer
        // -- "nothing explains today's move" -- and an absent section would
        // read as still loading.
        <div className="symbol-news-list">
          <h3 className="symbol-news-title">Recent News</h3>
          <div className="symbol-info-empty">No news since the last session.</div>
        </div>
      ) : (
        <div className="symbol-info-empty widget-empty">
          No company info or news available right now.
        </div>
      )}
    </div>
  );
}
