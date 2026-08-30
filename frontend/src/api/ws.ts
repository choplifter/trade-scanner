import ReconnectingWebSocket from "reconnecting-websocket";

import type { ChartSocketMessage, ScannerUpdateMessage } from "../types/alpaca";
import type { ReplayUpdateMessage } from "../types/replay";
import type { Screen, ScreenUpdateMessage } from "../types/screener";

const WS_OPEN = 1;

/** Everything /ws/scanners can push: a named view's rows (topic-keyed) or a
 * live user-defined screen's result (per-connection, not topic-keyed). */
type ScannerSocketMessage = ScannerUpdateMessage | ScreenUpdateMessage;

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
  protected socket: ReconnectingWebSocket;
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
      if (this.handleMessage(msg)) return;
      const key = this.topicKey(msg);
      this.listeners.get(key)?.forEach((fn) => fn(msg));
    });
    this.socket.addEventListener("open", () => {
      this.handleOpen();
      for (const topic of this.refCounts.keys()) {
        this.sendSubscribe(topic);
      }
    });
  }

  /** Subclass hook: return true to claim a message that isn't topic-keyed. */
  protected handleMessage(_msg: TMessage): boolean {
    return false;
  }

  /** Subclass hook: re-send any non-topic subscriptions after a reconnect.
   * The backend keeps no memory of what a dropped connection had asked for. */
  protected handleOpen(): void {}

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

/**
 * The scanner socket also carries live user-defined screens, which are not
 * topic-keyed: the server holds one screen per *connection* and pushes a
 * "screen_update" on each poll tick (see app/ws/screen_subscriptions.py),
 * because a screen's result is unique to whoever asked and can't be shared
 * across subscribers the way "scanner:gainers" can.
 *
 * One screen per socket, so subscribeScreen replaces rather than adds --
 * matching the server, which does the same. That's the right semantics for
 * the unified scanner widget, where one filter set is active at a time.
 */
class ScannerSocket extends TopicSocket<ScannerSocketMessage> {
  private screenListeners = new Set<(msg: ScreenUpdateMessage) => void>();
  private activeScreen: Screen | null = null;

  protected handleMessage(msg: ScannerSocketMessage): boolean {
    if (msg.type !== "screen_update") return false;
    this.screenListeners.forEach((fn) => fn(msg));
    return true;
  }

  protected handleOpen(): void {
    if (this.activeScreen) this.sendScreen(this.activeScreen);
  }

  private sendScreen(screen: Screen) {
    if (this.socket.readyState === WS_OPEN) {
      this.socket.send(JSON.stringify({ type: "screen", screen }));
    }
  }

  subscribeScreen(screen: Screen, listener: (msg: ScreenUpdateMessage) => void): () => void {
    this.screenListeners.add(listener);
    this.activeScreen = screen;
    // If the socket isn't open yet, handleOpen re-sends it on connect.
    this.sendScreen(screen);

    return () => {
      this.screenListeners.delete(listener);
      if (this.screenListeners.size === 0) {
        this.activeScreen = null;
        if (this.socket.readyState === WS_OPEN) {
          this.socket.send(JSON.stringify({ type: "unscreen" }));
        }
      }
    };
  }
}

export const scannerSocket = new ScannerSocket(
  "/ws/scanners",
  // Screen updates are claimed by handleMessage before this runs, so they
  // never need a topic; the empty string is unreachable in practice.
  (msg) => ("scanner" in msg ? msg.scanner : ""),
  "scanner",
);

export const chartSocket = new TopicSocket<ChartSocketMessage>(
  "/ws/chart",
  (msg) => msg.symbol,
  "symbol",
);

/** History-replay's counterpart to scannerSocket -- topic-keyed the same
 * way ("gainers"/"losers"/"most_active"), but scoped server-side to the
 * connected user's own session (see ws/replay_ws.py), so unlike
 * scannerSocket there's no shared-screen message type to claim here. */
export const replaySocket = new TopicSocket<ReplayUpdateMessage>(
  "/ws/replay",
  (msg) => msg.scanner,
  "scanner",
);
