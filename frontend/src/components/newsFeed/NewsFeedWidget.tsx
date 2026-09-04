import { useState } from "react";

import { useNewsFeed } from "../../hooks/useNewsFeed";
import { newsAge } from "../../utils/format";
import { symbolDragProps } from "../../utils/dragSymbol";

interface NewsFeedWidgetProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

const FILTER_KEY = "newsFeed:rankedOnly";
/** Symbol chips shown per row before the rest collapse into "+n". */
const MAX_CHIPS = 3;

function loadRankedOnly(): boolean {
  try {
    return localStorage.getItem(FILTER_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * A live wire of the newest articles across the whole market -- Alpaca's
 * news websocket, with the scanner engine's once-a-minute poll behind it
 * (backend app.market_data.news_stream / news_feed.NewsFeedTracker). The
 * "Ranked" filter narrows it to articles tagged to a symbol currently in
 * a fixed scanner view, which is what this widget used to show
 * exclusively. Distinct from SymbolInfoPanel, which shows several
 * articles for whichever *one* symbol is selected; this shows one row
 * per article across all of them, independent of selection, and updates
 * live without a click.
 *
 * One row per article: the symbols it names are chips (the first one is
 * the row's click/drag target, the rest clickable too), so a market-wide
 * article naming three names is one line, not three.
 */
export function NewsFeedWidget({ selectedSymbol, onSelectSymbol }: NewsFeedWidgetProps) {
  const [rankedOnly, setRankedOnly] = useState<boolean>(loadRankedOnly);
  const { items, loading, streamConnected } = useNewsFeed(50, rankedOnly);

  const setFilter = (value: boolean) => {
    setRankedOnly(value);
    try {
      localStorage.setItem(FILTER_KEY, value ? "1" : "0");
    } catch {
      // Works for this session, just not remembered next time.
    }
  };

  return (
    <div className="widget news-feed-widget">
      <div className="widget-header">
        <h2>News Feed</h2>
        <span className="widget-count">{items.length}</span>
        <div className="timeframe-selector">
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={!rankedOnly}
            onClick={() => setFilter(false)}
            title="Every article Alpaca publishes, market-wide"
          >
            All
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={rankedOnly}
            onClick={() => setFilter(true)}
            title="Only articles naming a symbol currently ranked in a scanner view"
          >
            Ranked
          </button>
        </div>
        <span
          className={`news-feed-stream${streamConnected ? " live" : ""}`}
          title={streamConnected ? "Live: Alpaca news websocket connected" : "Polling once a minute (news websocket not connected)"}
        >
          {streamConnected ? "● live" : "○ poll"}
        </span>
      </div>
      <div className="widget-body">
        {loading && items.length === 0 ? (
          <div className="widget-empty">Loading…</div>
        ) : items.length === 0 ? (
          <div className="widget-empty">
            {rankedOnly
              ? "No news yet for the currently ranked symbols -- switch to All for the whole market."
              : "No news yet -- new articles appear here as they're published."}
          </div>
        ) : (
          <ul className="news-feed-list">
            {items.map((item) => {
              const chips = item.symbols.slice(0, MAX_CHIPS);
              const more = item.symbols.length - chips.length;
              const selected = selectedSymbol != null && item.symbols.includes(selectedSymbol);
              return (
                <li
                  key={item.id}
                  className="news-feed-item"
                  aria-selected={selected}
                  onClick={() => {
                    if (item.symbol) onSelectSymbol(item.symbol);
                  }}
                >
                  <span className="news-feed-symbols">
                    {chips.length === 0 ? (
                      <span className="news-feed-symbol none" title="No symbol tagged">
                        —
                      </span>
                    ) : (
                      chips.map((symbol) => (
                        <span
                          key={symbol}
                          className={`news-feed-symbol${item.ranked ? " ranked" : ""}`}
                          title={`${symbol} -- click to select, drag onto a chart`}
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelectSymbol(symbol);
                          }}
                          {...symbolDragProps(symbol)}
                        >
                          {symbol}
                        </span>
                      ))
                    )}
                    {more > 0 && (
                      <span className="news-feed-symbol more" title={item.symbols.slice(MAX_CHIPS).join(", ")}>
                        +{more}
                      </span>
                    )}
                  </span>
                  {item.url ? (
                    <a
                      className="news-feed-headline"
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {item.headline}
                    </a>
                  ) : (
                    <span className="news-feed-headline">{item.headline}</span>
                  )}
                  <span className="news-feed-meta">
                    {item.source} · {newsAge(item.published_at)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
