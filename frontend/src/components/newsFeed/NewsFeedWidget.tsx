import { useNewsFeed } from "../../hooks/useNewsFeed";
import { newsAge } from "../../utils/format";

interface NewsFeedWidgetProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

/**
 * A live wire of recently-discovered news across every symbol currently
 * ranked in a fixed scanner view -- see useNewsFeed/backend
 * app.market_data.news_feed.NewsFeedTracker. Distinct from
 * SymbolInfoPanel, which shows several articles for whichever *one*
 * symbol is selected; this shows one article per row across *all* of
 * them, independent of selection, and updates live without a click.
 *
 * One row per (article, symbol) pair, not one row per article -- the same
 * article tagged to several ranked symbols appears once per symbol, so
 * every row's click target is unambiguous (see useNewsFeed's own
 * docstring for why this was the chosen granularity over a compacted
 * multi-symbol row).
 */
export function NewsFeedWidget({ selectedSymbol, onSelectSymbol }: NewsFeedWidgetProps) {
  const { items, loading } = useNewsFeed();

  return (
    <div className="widget news-feed-widget">
      <div className="widget-header">
        <h2>News Feed</h2>
        <span className="widget-count">{items.length}</span>
      </div>
      <div className="widget-body">
        {loading && items.length === 0 ? (
          <div className="widget-empty">Loading…</div>
        ) : items.length === 0 ? (
          <div className="widget-empty">
            No news yet for the currently ranked symbols -- new articles appear here as they're
            published.
          </div>
        ) : (
          <ul className="news-feed-list">
            {items.map((item) => (
              <li
                key={item.id}
                className="news-feed-item"
                aria-selected={item.symbol === selectedSymbol}
                onClick={() => onSelectSymbol(item.symbol)}
              >
                <span className="news-feed-symbol">{item.symbol}</span>
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
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
