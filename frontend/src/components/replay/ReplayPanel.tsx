import { useEffect, useState } from "react";

import {
  getReplayStateOrNull,
  pauseReplay,
  playReplay,
  seekReplay,
  setReplaySpeed,
  startReplay,
  stepReplay,
  stopReplay,
} from "../../api/replay";
import { useReplayFeed } from "../../hooks/useReplayFeed";
import { useReplaySession } from "../../hooks/useReplaySession";
import { useTradingMode } from "../../hooks/useTradingMode";
import { TICKER_RE } from "../../utils/dragSymbol";
import { ScannerTable } from "../scanner/ScannerTable";

interface ReplayPanelProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

const VIEWS = [
  { key: "gainers", label: "Gainers" },
  { key: "losers", label: "Losers" },
  { key: "most_active", label: "Most Active" },
] as const;

const SPEED_CHOICES = [1, 2, 5, 10, 20];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function parseSymbols(raw: string): string[] {
  const seen = new Set<string>();
  for (const token of raw.split(/[\s,]+/)) {
    const upper = token.trim().toUpperCase();
    if (upper && TICKER_RE.test(upper)) seen.add(upper);
  }
  return [...seen];
}

/** Start form + play/pause/seek/speed controls + the three replayed ranked
 * views, sourced from backend/app/replay (see its module docstrings). A
 * self-contained dashboard widget, same footing as WatchlistPanel/
 * ScannerBenchmarkWidget -- see hooks/useDashboardLayout.ts's "replay" id.
 *
 * selectedSymbol/onSelectSymbol are the same app-wide selection the main
 * ChartWidget reads -- clicking a replayed row here loads that symbol into
 * the *main* chart up top, same as clicking any other scanner row always
 * has. There's no second chart here: when the selected symbol is part of
 * the active replay session, ChartWidget itself switches its intraday
 * bars to the clipped-to-as_of replay source instead of live data (see
 * ChartWidget's own isReplaySymbol) -- one chart, not two, so trading it
 * works exactly the way trading any other symbol already does (the Order
 * Ticket panel, not the chart itself).
 *
 * Symbols are optional: leaving the field blank replays the real
 * historical "stocks in play" for that date -- whatever the live scanner
 * actually flagged, pulled from ScannerHistoryStore.symbols_for_date (see
 * routers/replay.py's /start) -- rather than requiring the user to already
 * know in advance which symbols were worth watching. Typing symbols still
 * works, for replaying a specific watchlist instead.
 *
 * Meaningful mainly in Simulation Mode -- a replayed order only actually
 * fills against replayed prices when trading through Simulation Mode's
 * local order book (see routers/trading_sim.py's _replay_seam) -- so this
 * shows a pointer to switch modes rather than the controls when live.
 *
 * Idle (no session) renders as a single-row strip -- just the header and a
 * compact inline start form, no controls/table/hints below it -- since most
 * sessions of this app aren't actively replaying most of the time. See
 * App.tsx's AppShell for how panels-mode layout additionally reclaims the
 * screen space this frees up (grid mode's cell height is user-owned drag
 * state and is deliberately left alone here).
 */
export function ReplayPanel({ selectedSymbol, onSelectSymbol }: ReplayPanelProps) {
  const tradingMode = useTradingMode();
  const session = useReplaySession();
  const [activeView, setActiveView] = useState<(typeof VIEWS)[number]["key"]>("gainers");
  const [symbolsInput, setSymbolsInput] = useState("");
  const [startDate, setStartDate] = useState(todayIso());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<{ start: string | null; end: string | null }>({
    start: null,
    end: null,
  });
  // Local scrub position while dragging the seek slider -- null means "not
  // scrubbing, show session.as_of instead". Committing a seekReplay() call
  // per onChange (one per pixel of drag) would both hammer the backend and
  // disable the input mid-drag once `busy` flips true, which interrupts the
  // browser's own pointer capture. Dragging only updates this local value;
  // the request fires once, on release.
  const [scrubMs, setScrubMs] = useState<number | null>(null);

  // Hydrates the replayMode singleton (and this component's own `session`,
  // via useReplaySession) from the backend on mount -- otherwise a page
  // refresh mid-session would show the start form even though the backend
  // still has one, until the user's next replay action happened to sync it.
  useEffect(() => {
    getReplayStateOrNull().then((res) => {
      if (res) setRange(res.range);
    });
  }, []);

  const feed = useReplayFeed(session ? activeView : null);
  // session.as_of is kept live by useReplayFeed's own tick handler calling
  // api/replayMode.ts's updateReplayAsOf on every WS message -- so reading
  // it straight off `session` here (rather than off `feed.asOf`) already
  // reflects the pacing loop's own advances while playing, not just
  // whatever it was at the last explicit REST call.
  const currentAsOf = session?.as_of ?? null;

  async function run<T>(action: () => Promise<T>): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const res = await action();
      if (res && typeof res === "object" && "range" in res) {
        setRange((res as { range: { start: string | null; end: string | null } }).range);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Replay action failed");
    } finally {
      setBusy(false);
    }
  }

  const symbols = parseSymbols(symbolsInput);

  // Left/Right steps the replay clock one bar at a time -- same
  // not-while-typing guard TradingWidget.tsx's own hotkeys use, which as a
  // side effect also excludes the focused seek slider above (an <input>
  // itself), so its own native arrow-key nudging never double-fires
  // alongside this.
  useEffect(() => {
    if (!session) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const target = document.activeElement;
      const isTyping =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (isTyping || busy) return;

      if (e.key === "ArrowRight") {
        e.preventDefault();
        void run(() => stepReplay("forward"));
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        void run(() => stepReplay("backward"));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [session, busy]);

  return (
    <div className={session ? "widget replay-widget" : "widget replay-widget replay-collapsed"}>
      <div className="widget-header">
        <h2>History Replay</h2>
        {session ? (
          <span className="widget-count" title="Symbols in this replay session">
            {session.symbols.length}
          </span>
        ) : (
          <div className="replay-start-form">
            <label>
              <span>Start date</span>
              <input
                type="date"
                value={startDate}
                max={todayIso()}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </label>
            <label>
              <span>Symbols <span className="replay-optional-hint">(optional)</span></span>
              <input
                type="text"
                placeholder="Leave blank for that day's actual stocks in play"
                value={symbolsInput}
                onChange={(e) => setSymbolsInput(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="tab"
              disabled={busy}
              onClick={() => run(() => startReplay(startDate, symbols))}
            >
              {busy ? "Loading…" : "Start replay"}
            </button>
          </div>
        )}
      </div>

      {error && <p className="widget-error">{error}</p>}

      {session && (
        <>
          {tradingMode.mode !== "simulation" ? (
            // Starting a replay switches to Simulation on its own (see
            // api/replay.ts); this is the way back after switching away.
            <div className="widget-empty replay-mode-hint">
              {tradingMode.mode === "live" ? "Live" : "Paper"} mode is on: the option chain and tickets show the
              real account, not the replayed moment.{" "}
              <button type="button" className="row-action" onClick={() => tradingMode.setMode("simulation")}>
                Switch to Simulation
              </button>
            </div>
          ) : (
            <p className="screener-summary">
              Simulation mode is on for this replay -- chart, option chain and orders follow the clock. Stop
              returns to the previous mode.
            </p>
          )}
          <div className="replay-controls">
            <button
              type="button"
              className="tab"
              disabled={busy}
              title="Step back one bar (←)"
              onClick={() => run(() => stepReplay("backward"))}
            >
              ◀ Step
            </button>
            <button
              type="button"
              className="tab"
              disabled={busy}
              onClick={() => run(session.playing ? pauseReplay : playReplay)}
            >
              {session.playing ? "Pause" : "Play"}
            </button>
            <button
              type="button"
              className="tab"
              disabled={busy}
              title="Step forward one bar (→)"
              onClick={() => run(() => stepReplay("forward"))}
            >
              Step ▶
            </button>
            <select
              value={session.speed}
              disabled={busy}
              onChange={(e) => run(() => setReplaySpeed(Number(e.target.value)))}
            >
              {SPEED_CHOICES.map((s) => (
                <option key={s} value={s}>
                  {s}x
                </option>
              ))}
            </select>
            {range.start && range.end && (
              <input
                className="replay-seek"
                type="range"
                min={Date.parse(range.start)}
                max={Date.parse(range.end)}
                step={5 * 60 * 1000}
                value={scrubMs ?? (currentAsOf ? Date.parse(currentAsOf) : Date.parse(range.start))}
                onChange={(e) => setScrubMs(Number(e.target.value))}
                onMouseUp={(e) => {
                  const ms = Number(e.currentTarget.value);
                  setScrubMs(null);
                  run(() => seekReplay(new Date(ms).toISOString()));
                }}
                onTouchEnd={(e) => {
                  const ms = Number(e.currentTarget.value);
                  setScrubMs(null);
                  run(() => seekReplay(new Date(ms).toISOString()));
                }}
                onKeyUp={(e) => {
                  const ms = Number(e.currentTarget.value);
                  setScrubMs(null);
                  run(() => seekReplay(new Date(ms).toISOString()));
                }}
              />
            )}
            <span className="replay-as-of">
              {currentAsOf ? new Date(currentAsOf).toLocaleString() : "—"}
            </span>
            <button
              type="button"
              className="tab"
              disabled={busy}
              onClick={() => run(stopReplay)}
              title="End this replay session"
            >
              Stop
            </button>
          </div>

          <p className="screener-summary">
            Reduced fidelity: no news, momentum alarm, strategy signals, or fundamentals --
            replayed from 5-minute bars only.
          </p>

          <div className="scanner-tabs replay-view-tabs">
            {VIEWS.map((v) => (
              <button
                key={v.key}
                type="button"
                className={activeView === v.key ? "tab active" : "tab"}
                aria-pressed={activeView === v.key}
                onClick={() => setActiveView(v.key)}
              >
                {v.label}
              </button>
            ))}
          </div>

          <div className="widget-body">
            {feed.loading ? (
              <div className="widget-empty">Loading…</div>
            ) : (
              <ScannerTable rows={feed.rows} selectedSymbol={selectedSymbol} onSelectSymbol={onSelectSymbol} />
            )}
          </div>
        </>
      )}
    </div>
  );
}
