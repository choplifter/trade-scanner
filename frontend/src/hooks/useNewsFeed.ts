import { useEffect, useState } from "react";

import { getRecentNewsFeed } from "../api/http";
import { newsFeedSocket } from "../api/ws";
import type { NewsFeedItem } from "../types/newsFeed";

export interface NewsFeedState {
  items: NewsFeedItem[];
  loading: boolean;
  /** Whether the backend's news websocket is connected (else the feed is
   * the once-a-minute poll only). */
  streamConnected: boolean;
}

/**
 * The live market-wide news feed: REST-fetch-then-WS-subscribe, same
 * shape as useScannerFeed -- an initial GET seeds the list, then the
 * socket only ever prepends newly-discovered items (it's push-only, see
 * ws/news_feed_ws.py's own docstring for why there's no snapshot-on-
 * subscribe to reconcile against). Deduped by id in case the REST
 * response and an early WS push briefly overlap.
 *
 * `rankedOnly` narrows the feed to articles tagged to a symbol currently
 * ranked in a fixed scanner view: the REST seed asks the backend, pushes
 * are filtered on their own `ranked` flag.
 *
 * Each push is merged in by published_at, not unshifted onto the front --
 * a later poll can surface an article whose published_at predates one
 * already in the list (syndication/backfill lag on Alpaca's side; see
 * NewsFeedTracker.recent()'s docstring for the same issue server-side),
 * so discovery order isn't a reliable stand-in for display order here.
 */
function byPublishedAtDesc(a: NewsFeedItem, b: NewsFeedItem): number {
  return new Date(b.published_at).getTime() - new Date(a.published_at).getTime();
}

export function useNewsFeed(limit = 50, rankedOnly = false): NewsFeedState {
  const [items, setItems] = useState<NewsFeedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [streamConnected, setStreamConnected] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    getRecentNewsFeed(limit, rankedOnly)
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setStreamConnected(res.stream_connected ?? false);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    const unsubscribe = newsFeedSocket.subscribe("feed", (msg) => {
      if (rankedOnly && !msg.item.ranked) return;
      setItems((prev) => {
        if (prev.some((i) => i.id === msg.item.id)) return prev;
        return [...prev, msg.item].sort(byPublishedAtDesc).slice(0, limit);
      });
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [limit, rankedOnly]);

  return { items, loading, streamConnected };
}
