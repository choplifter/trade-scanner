import { useEffect, useRef } from "react";

import type { SymbolInfoState } from "../../hooks/useSymbolInfo";
import { newsAge } from "../../utils/format";

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

export function SymbolInfoPanel({ symbol, state, highlightTimes }: SymbolInfoPanelProps) {
  const { info, loading, error } = state;
  const highlighted = new Set(highlightTimes ?? []);
  const firstHighlightRef = useRef<HTMLDivElement | null>(null);

  // On a new click (new array identity), bring the story into view -- the
  // panel sits below the chart and is usually scrolled elsewhere. Scrolled
  // by hand, vertically, on the panel alone: scrollIntoView walks *every*
  // scrollable ancestor in both axes, and a news click was observed
  // dragging the chart sideways on the way.
  useEffect(() => {
    const el = firstHighlightRef.current;
    if (!el) return;
    const panel = el.closest(".symbol-info-panel") as HTMLElement | null;
    if (!panel) return;
    const delta = el.getBoundingClientRect().top - panel.getBoundingClientRect().top;
    const height = el.getBoundingClientRect().height;
    if (delta < 0 || delta + height > panel.clientHeight) {
      panel.scrollTo({ top: panel.scrollTop + delta - 8, behavior: "smooth" });
    }
  }, [highlightTimes]);

  if (!symbol) return null;
  if (error) return null; // ChartWidget's own error state already covers this symbol
  if (loading && !info) {
    return <div className="symbol-info-panel widget-empty">Loading company info…</div>;
  }
  if (!info) return null;

  const headerBits = [info.company_name, info.sector, info.industry].filter(Boolean);
  const hasProfile = headerBits.length > 0 || !!info.description || !!info.logo_url;

  return (
    <div className="symbol-info-panel">
      {hasProfile && (
        <div className="symbol-info-header">
          <div className="symbol-info-title-row">
            {info.logo_url && (
              <img
                src={info.logo_url}
                alt=""
                className="symbol-info-logo"
                onError={(e) => {
                  e.currentTarget.style.display = "none";
                }}
              />
            )}
            {headerBits.length > 0 && <span className="symbol-info-tags">{headerBits.join(" · ")}</span>}
          </div>
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
