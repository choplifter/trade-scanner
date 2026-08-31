/** One (article, symbol) pair discovered by the live cross-symbol news
 * feed -- see backend app.market_data.news_feed.NewsFeedItem. The same
 * article tagged to several symbols yields one of these per symbol. */
export interface NewsFeedItem {
  id: string;
  article_id: string;
  symbol: string;
  headline: string;
  source: string;
  url: string | null;
  published_at: string;
  discovered_at: string;
}

/** Pushed over /ws/news-feed as new articles are discovered -- push-only,
 * no snapshot-on-subscribe (see ws/news_feed_ws.py); GET /api/news-feed/recent
 * seeds the initial list instead. */
export interface NewsFeedItemMessage {
  type: "news_feed_item";
  item: NewsFeedItem;
}
