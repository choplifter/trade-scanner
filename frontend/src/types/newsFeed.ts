/** One article discovered by the live news feed -- see backend
 * app.market_data.news_feed.NewsFeedItem. Market-wide: an article may
 * name several symbols or none; `symbol` is the first one (the row's
 * click/drag target), `symbols` all of them, `ranked` whether any of them
 * is currently in a fixed scanner view. */
export interface NewsFeedItem {
  id: string;
  article_id: string;
  symbol: string;
  symbols: string[];
  ranked: boolean;
  headline: string;
  source: string;
  url: string | null;
  published_at: string;
  discovered_at: string;
}

/** Pushed over /ws/news-feed as new articles arrive (Alpaca's news
 * websocket, or the once-a-minute poll behind it) -- push-only, no
 * snapshot-on-subscribe (see ws/news_feed_ws.py); GET /api/news-feed/recent
 * seeds the initial list instead. */
export interface NewsFeedItemMessage {
  type: "news_feed_item";
  item: NewsFeedItem;
}
