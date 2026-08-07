import ReconnectingWebSocket from "reconnecting-websocket";

import type { ChartSocketMessage, ScannerUpdateMessage } from "../types/alpaca";

const WS_OPEN = 1;

function wsUrl(path: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

/**
 * One shared, reconnecting socket per endpoint, multiplexed by topic
 * (scanner name or symbol) so several widgets share a single connection.
 * Ref-counts subscribers per topic and re-sends subscribe messages after a
 * reconnect, since the backend has no memory of what a fresh connection
 * previously asked for.
 */
class TopicSocket<TMessage extends { type: string }> {
  private socket: ReconnectingWebSocket;
  private listeners = new Map<string, Set<(msg: TMessage) => void>>();
  private refCounts = new Map<string, number>();

  constructor(
    path: string,
    private readonly topicKey: (msg: TMessage) => string,
    private readonly subscribeParamKey: string,
  ) {
    this.socket = new ReconnectingWebSocket(wsUrl(path));
    this.socket.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data as string) as TMessage;
      const key = this.topicKey(msg);
      this.listeners.get(key)?.forEach((fn) => fn(msg));
    });
    this.socket.addEventListener("open", () => {
      for (const topic of this.refCounts.keys()) {
        this.sendSubscribe(topic);
      }
    });
  }

  private sendSubscribe(topic: string) {
    this.socket.send(JSON.stringify({ type: "subscribe", [this.subscribeParamKey]: topic }));
  }

  private sendUnsubscribe(topic: string) {
    if (this.socket.readyState === WS_OPEN) {
      this.socket.send(JSON.stringify({ type: "unsubscribe", [this.subscribeParamKey]: topic }));
    }
  }

  subscribe(topic: string, listener: (msg: TMessage) => void): () => void {
    if (!this.listeners.has(topic)) this.listeners.set(topic, new Set());
    this.listeners.get(topic)!.add(listener);

    const refCount = (this.refCounts.get(topic) ?? 0) + 1;
    this.refCounts.set(topic, refCount);
    // refCounts is updated synchronously above, so if the socket isn't open
    // yet, the constructor's shared "open" listener will pick this topic up
    // and send its subscribe message once the connection completes -- no
    // separate per-call listener needed.
    if (refCount === 1 && this.socket.readyState === WS_OPEN) {
      this.sendSubscribe(topic);
    }

    return () => {
      this.listeners.get(topic)?.delete(listener);
      const remaining = (this.refCounts.get(topic) ?? 1) - 1;
      if (remaining <= 0) {
        this.refCounts.delete(topic);
        this.listeners.delete(topic);
        this.sendUnsubscribe(topic);
      } else {
        this.refCounts.set(topic, remaining);
      }
    };
  }
}

export const scannerSocket = new TopicSocket<ScannerUpdateMessage>(
  "/ws/scanners",
  (msg) => msg.scanner,
  "scanner",
);

export const chartSocket = new TopicSocket<ChartSocketMessage>(
  "/ws/chart",
  (msg) => msg.symbol,
  "symbol",
);
