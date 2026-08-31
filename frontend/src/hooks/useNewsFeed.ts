import { useEffect, useState } from "react";

import { getRecentNewsFeed } from "../api/http";
import { newsFeedSocket } from "../api/ws";
import type { NewsFeedItem } from "../types/newsFeed";

export interface NewsFeedState {
  items: NewsFeedItem[];
  loading: boolean;
}

/**
 * The live cross-symbol news feed: REST-fetch-then-WS-subscribe, same
 * shape as useScannerFeed -- an initial GET seeds the list, then the
 * socket only ever prepends newly-discovered items (it's push-only, see
 * ws/news_feed_ws.py's own docstring for why there's no snapshot-on-
 * subscribe to reconcile against). Deduped by id in case the REST
 * response and an early WS push briefly overlap.
 */
export function useNewsFeed(limit = 50): NewsFeedState {
  const [items, setItems] = useState<NewsFeedItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    getRecentNewsFeed(limit)
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    const unsubscribe = newsFeedSocket.subscribe("feed", (msg) => {
      setItems((prev) =>
        prev.some((i) => i.id === msg.item.id) ? prev : [msg.item, ...prev].slice(0, limit),
      );
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [limit]);

  return { items, loading };
}
