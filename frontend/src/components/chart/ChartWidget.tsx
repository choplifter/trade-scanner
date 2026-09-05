import type { FormEvent, DragEvent } from "react";
import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { createPortal } from "react-dom";

import { useSymbolInfoContext } from "../../context/SymbolInfoContext";
import { TICKER_RE, isSymbolDrag, readDroppedSymbol } from "../../utils/dragSymbol";
import { formatLeg, parseOcc } from "../../utils/occ";
import { ContractTicket } from "./ContractTicket";
import { useTradingContext } from "../../context/TradingContext";
import { useSpreadLevels } from "../../hooks/useSpreadLevels";
import { useTradingMode } from "../../hooks/useTradingMode";
import { useChartFeed } from "../../hooks/useChartFeed";
import {
  gexLevelsFrom,
  useGexReading,
} from "../../hooks/useGexLevels";
import type { ChartFocus } from "../../types/screener";
import { useHistoricalBars } from "../../hooks/useHistoricalBars";
import { useReplayBars } from "../../hooks/useReplayBars";
import { useReplayIndicators } from "../../hooks/useReplayIndicators";
import { useReplaySession } from "../../hooks/useReplaySession";
import { exitsForPosition, num } from "../../types/trading";
import { aggregateBars, TIMEFRAME_OPTIONS } from "../../utils/aggregateBars";
import { PREMIUM_LEVEL_NAMES, usePremiumLevels, usePremiumSeries } from "../../hooks/usePremiumLevels";
import { formatPrice, newsAge } from "../../utils/format";
import { CandleChart, POSITION_ENTRY_COLOR, positionStopColor, positionTargetColor, type OrderLevel } from "./CandleChart";
import { getSettings } from "../../api/settings";
import { useChartPalette, useSettings } from "../../hooks/useSettings";
import type { ChartType, CursorMode, PositionLevels } from "./CandleChart";

interface ChartWidgetProps {
  symbol: string | null;
  /** Set when a backtest pick or a journal trade is clicked: jump to this
   * entry (and exit, for a trade) and show it at a resolution where it's
   * visible. */
  focus?: ChartFocus | null;
  /** Called when the user takes manual control of the timeframe while a
   * focus is active -- see the timeframe buttons' onClick for why. */
  onClearFocus?: () => void;
  /** Lets the premium chart of an option contract jump to its underlying. */
  onSelectSymbol?: (symbol: string) => void;
  /** A copy opened from the dock's tab menu: pinned to its own symbol
   * (typed into the header or dropped from a scanner row) instead of
   * following the scanner's selection. */
  pinned?: boolean;
}

const DEFAULT_TIMEFRAME_KEY = "5m";

// Which indicators the reader has checked on in the Levels dropdown, by
// name. Persisted because "show me only the strategy" is a way of working,
// not a property of the symbol on screen -- it should survive a reload and a
// symbol change the way the timeframe and chart type already do. Stored as
// the *visible* set, not a hidden one: the control is a checklist, so each
// row's checked state has to read directly off this.
const VISIBLE_INDICATORS_KEY = "chart:visibleIndicators";
const PREMIUM_LEVELS_SEEDED_KEY = "chart:premiumLevelsSeeded";

type TradeLevelKey = "entry" | "stop" | "target";
const ALL_TRADE_LEVEL_KEYS: TradeLevelKey[] = ["entry", "stop", "target"];
const VISIBLE_TRADE_LEVELS_KEY = "chart:visibleTradeLevels";

function tradeLevelItems(): { key: TradeLevelKey; label: string; color: string }[] {
  return [
    { key: "entry", label: "Entry", color: POSITION_ENTRY_COLOR },
    { key: "stop", label: "Stop", color: positionStopColor() },
    { key: "target", label: "Target", color: positionTargetColor() },
  ];
}

function loadVisibleIndicators(): Set<string> {
  try {
    const raw = localStorage.getItem(VISIBLE_INDICATORS_KEY);
    return new Set<string>(raw ? JSON.parse(raw) : []);
  } catch {
    // Private browsing, storage disabled, or a value from an older shape --
    // none of which is worth failing a chart over.
    return new Set<string>();
  }
}

// Trade levels default to all-visible -- before this control existed, an
// open position's entry/stop/target always drew unconditionally.
function loadVisibleTradeLevels(): Set<TradeLevelKey> {
  try {
    const raw = localStorage.getItem(VISIBLE_TRADE_LEVELS_KEY);
    if (!raw) return new Set(ALL_TRADE_LEVEL_KEYS);
    const parsed = JSON.parse(raw) as unknown[];
    return new Set(parsed.filter((k): k is TradeLevelKey => ALL_TRADE_LEVEL_KEYS.includes(k as TradeLevelKey)));
  } catch {
    return new Set(ALL_TRADE_LEVEL_KEYS);
  }
}

function persistLevelSet(key: string, values: Set<string>) {
  try {
    localStorage.setItem(key, JSON.stringify([...values]));
  } catch {
    // The toggle still works for this session; it just will not be
    // remembered next time.
  }
}

const CURSOR_KEY = "chart:cursorMode";

const CURSOR_MODES: { key: CursorMode; label: string; title: string }[] = [
  {
    key: "pointer",
    label: "Pointer",
    title: "No crosshair -- an ordinary arrow, for when the lines are in the way",
  },
  {
    key: "crosshair",
    label: "Crosshair",
    title: "Crosshair follows the mouse freely",
  },
  {
    key: "magnet",
    label: "Magnet",
    title:
      "Crosshair snaps to the nearest open/high/low/close -- the one that reads an exact price off a candle instead of guessing at a few pixels per cent",
  },
];

function loadCursorMode(): CursorMode {
  try {
    const saved = localStorage.getItem(CURSOR_KEY);
    return CURSOR_MODES.some((m) => m.key === saved) ? (saved as CursorMode) : "magnet";
  } catch {
    return "magnet";
  }
}

const CHART_TYPES: { key: ChartType; label: string; title: string }[] = [
  { key: "candles", label: "Candles", title: "Open/high/low/close candles" },
  {
    key: "line",
    label: "Line",
    title: "Closing price only -- the shape of the move without the wicks",
  },
];

export function ChartWidget({ symbol, focus, onClearFocus, onSelectSymbol, pinned = false }: ChartWidgetProps) {
  // An OCC symbol: the chart shows the contract's premium, not a stock.
  // Memoized on the symbol: a fresh object per render would give the
  // premium levels (and so CandleChart's price lines) a new identity on
  // every trade tick.
  const contract = useMemo(() => (symbol ? parseOcc(symbol) : null), [symbol]);
  // A symbol (or option contract) dragged from a scanner, watchlist, order
  // or option-chain row and dropped on the chart loads it -- for the main
  // chart that is the dashboard's selection, for a pinned copy its own.
  const [dropActive, setDropActive] = useState(false);
  const dropProps = onSelectSymbol
    ? {
        onDragOver: (e: DragEvent<HTMLDivElement>) => {
          if (!isSymbolDrag(e)) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          if (!dropActive) setDropActive(true);
        },
        onDragLeave: (e: DragEvent<HTMLDivElement>) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropActive(false);
        },
        onDrop: (e: DragEvent<HTMLDivElement>) => {
          setDropActive(false);
          const dropped = readDroppedSymbol(e);
          if (!dropped) return;
          e.preventDefault();
          onSelectSymbol(dropped);
        },
      }
    : {};
  // The pinned chart's own symbol field.
  const [symbolDraft, setSymbolDraft] = useState(symbol ?? "");
  useEffect(() => setSymbolDraft(symbol ?? ""), [symbol]);
  const submitSymbol = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const next = symbolDraft.trim().toUpperCase();
    if (!next || !(TICKER_RE.test(next) || parseOcc(next))) return;
    onSelectSymbol?.(next);
  };
  // The Settings dialog's chart defaults seed a chart's own state; the
  // buttons in the chart change only this chart from then on.
  const [timeframeKey, setTimeframeKey] = useState(() => {
    const wanted = getSettings().defaultTimeframe;
    return TIMEFRAME_OPTIONS.some((o) => o.key === wanted) ? wanted : DEFAULT_TIMEFRAME_KEY;
  });

  // A pick carries the resolution that makes it legible -- a 10:35 intraday
  // entry means nothing on a daily chart, and a daily pick is a sliver on a
  // 5m one. Switching here rather than in the panel keeps the timeframe
  // control the single owner of that state, so the user can still change it
  // afterwards and stay changed.
  useEffect(() => {
    if (focus) setTimeframeKey(focus.timeframeKey);
  }, [focus]);
  // Off by default -- these are reference lines, not something every chart
  // view needs cluttered onto it.
  const [visibleIndicators, setVisibleIndicators] = useState<Set<string>>(loadVisibleIndicators);
  const [visibleTradeLevels, setVisibleTradeLevels] = useState<Set<TradeLevelKey>>(loadVisibleTradeLevels);
  // The Levels button just opens/closes this checklist -- there's no
  // separate master on/off any more, each row owns its own visibility.
  const [levelsMenuOpen, setLevelsMenuOpen] = useState(false);
  const levelsButtonRef = useRef<HTMLDivElement | null>(null);
  // The menu itself is rendered through a portal (see below for why) and so
  // needs its own ref for the outside-click check -- it's no longer a DOM
  // descendant of levelsButtonRef.
  const levelsMenuRef = useRef<HTMLDivElement | null>(null);
  // Screen position for the portaled menu, measured off the trigger button
  // when it opens.
  const [levelsMenuPos, setLevelsMenuPos] = useState<{ top: number; left: number } | null>(null);

  function toggleIndicatorVisible(name: string) {
    setVisibleIndicators((current) => {
      const next = new Set(current);
      if (!next.delete(name)) next.add(name);
      persistLevelSet(VISIBLE_INDICATORS_KEY, next);
      return next;
    });
  }

  function toggleTradeLevel(key: TradeLevelKey) {
    setVisibleTradeLevels((current) => {
      const next = new Set(current);
      if (!next.delete(key)) next.add(key);
      persistLevelSet(VISIBLE_TRADE_LEVELS_KEY, next);
      return next;
    });
  }

  // Measures where to portal the menu, and closes it on an outside click or
  // Escape -- there's no other affordance to dismiss it once open.
  useEffect(() => {
    if (!levelsMenuOpen) return;
    const button = levelsButtonRef.current;
    if (button) {
      const rect = button.getBoundingClientRect();
      setLevelsMenuPos({ top: rect.bottom + 4, left: rect.left });
    }
    function handlePointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (levelsButtonRef.current?.contains(target)) return;
      if (levelsMenuRef.current?.contains(target)) return;
      setLevelsMenuOpen(false);
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setLevelsMenuOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [levelsMenuOpen]);
  const option =
    TIMEFRAME_OPTIONS.find((o) => o.key === timeframeKey) ??
    TIMEFRAME_OPTIONS.find((o) => o.key === DEFAULT_TIMEFRAME_KEY)!;

  // Always kept live regardless of the selected timeframe: it's the source
  // for the intraday buckets below, and also drives the header's last-price
  // (which should track real trades even while looking at a Daily chart).
  // Which VWAP anchor the chart draws. Session (09:30) is the day-trading
  // convention and the default; premarket-anchored is what TradingView shows.
  // On a gapper these are genuinely different lines -- IPST 2026-08-17 closed
  // at 7.39 with the session line at 7.81 and the premarket one near 7.18.
  const [vwapFromPremarket, setVwapFromPremarket] = useState(() => getSettings().vwapAnchor === "premarket");
  // Kept across symbol and timeframe changes: how someone wants price drawn
  // is a preference, not a property of what they are looking at.
  const [chartType, setChartType] = useState<ChartType>(() => getSettings().defaultChartType);
  // Portals the whole widget to document.body and sizes it to the viewport
  // (see the return statement below) rather than rendering a second chart
  // instance in an overlay -- one CandleChart, one WS subscription, just
  // relocated in the DOM. Not persisted: fullscreen is a per-session action,
  // not a layout preference.
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    if (!isFullscreen) return;
    function handleFullscreenKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setIsFullscreen(false);
    }
    document.addEventListener("keydown", handleFullscreenKeyDown);
    return () => document.removeEventListener("keydown", handleFullscreenKeyDown);
  }, [isFullscreen]);
  // Magnet by default: the chart's main job here is checking whether a
  // strategy's line sits on the level it claims, and that needs an exact
  // read rather than a free-floating one.
  const [cursorMode, setCursorModeState] = useState<CursorMode>(loadCursorMode);
  // TradingView's auto-scroll: on, every new candle snaps the chart back to
  // the newest bar; off, the chart stays wherever it was dragged.
  const [autoScroll, setAutoScroll] = useState<boolean>(() => getSettings().autoScroll);
  const [appSettings] = useSettings();
  const { palette, hollow, dark } = useChartPalette();
  const numberLocale = appSettings.numberFormat === "point" ? "en-US" : appSettings.numberFormat === "comma" ? "de-DE" : undefined;

  function setCursorMode(mode: CursorMode) {
    setCursorModeState(mode);
    try {
      localStorage.setItem(CURSOR_KEY, mode);
    } catch {
      // Works for this session, just not remembered next time.
    }
  }
  // While the logged-in user has a replay session, the chart switches its
  // *intraday* bars (1m/5m/15m -- all clean multiples of replay's native
  // 5-minute resolution via aggregateBars below) to the clipped-to-as_of
  // replay source instead of live data -- see routers/replay.py's
  // /bars/{symbol} (ReplayEngine.bars_up_to). Any symbol, not just the
  // session's own: the backend fetches a symbol outside the session on
  // first request (ReplayEngine.ensure_bars), so SPY from the watchlist
  // replays without having been named at start. Only the ranked scanner
  // rows keep the session's universe. Historical-kind timeframes (1h and up)
  // keep showing real live data regardless: building those from 5-minute
  // bars would need multi-day aggregation this doesn't have, and there's
  // little use for a weekly candle while replaying a single session.
  //
  // An option contract replays whenever a session is active at all: its
  // 1-minute premium bars for the replayed day come from the same
  // endpoint (app.replay.options_engine), and neither the live option
  // stream nor the live weekly/hourly bars are touched -- both would leak
  // the future into the chart.
  //
  // Trading against what's on screen needs no separate wiring here --
  // positions/orders (below) already come from Simulation Mode, whose own
  // price seam (see routers/trading_sim.py's _replay_seam) already fills
  // sim orders against this same replayed price. The chart just has to
  // show the same data the fill priced against.
  const replaySession = useReplaySession();
  const isReplaySymbol = !!symbol && !!replaySession;
  const usingReplayBars = isReplaySymbol && option.kind === "intraday";
  const intraday = useChartFeed(usingReplayBars ? null : symbol);
  const historical = useHistoricalBars(
    symbol,
    option.kind === "historical" && !(contract && replaySession) ? (option.alpacaTimeframe ?? null) : null,
  );
  const replay = useReplayBars(usingReplayBars ? symbol : null, replaySession?.as_of ?? null);
  const replayIndicators = useReplayIndicators(usingReplayBars && !contract ? symbol : null, replaySession?.as_of ?? null);
  // Any optionable underlying has a GEX reading now -- the backend computes
  // one on request rather than only for a fixed list. Skipped while the
  // chart shows a contract's premium: the walls belong to the underlying's
  // price axis, and asking for an OCC symbol would only buy a failed fetch.
  // Same conditional-fetch shape as isReplaySymbol/usingReplayBars above.
  const { reading: gexReading } = useGexReading(contract ? null : symbol);
  // One reading, both consumers: the chart levels and the net-GEX badge
  // below. Two hooks here would mean two round trips per symbol change.
  const gexLevels = gexLevelsFrom(gexReading);
  // The Options widget's strikes and armed underlying bounds, drawn like
  // the GEX walls (see useSpreadLevels).
  const spreadLevels = useSpreadLevels(symbol);
  // Shared with SymbolInfoWidget via context rather than a second
  // useSymbolInfo(symbol) call here -- the chart marks the same news on its
  // timeline (newsMarkers below), and two hook callers would double-fetch.
  // See SymbolInfoContext for why this can't just be threaded through
  // App.tsx's memoized `widgets` object instead.
  const { symbolInfo, setHighlightedNews } = useSymbolInfoContext();
  // A rejected drag (wrong side of market, order already filled, trading
  // switched off, ...) needs somewhere to surface without switching to the
  // Positions tab -- same .order-rejection + Dismiss pattern TradingWidget's
  // own instantError uses. A draft (indicative) line's drag never reaches
  // here: there's no order to reject, just a ticket field to overwrite.
  // Cleared on symbol change for the same reason as highlightedNews above.
  const [dragError, setDragError] = useState<string | null>(null);
  const tradingMode = useTradingMode();
  useEffect(() => setDragError(null), [symbol]);

  // Entry/stop/target lines for whatever position is open on the symbol on
  // screen -- via context rather than a second useTrading() call, which
  // would be an independent, out-of-sync poll loop (see TradingContext).
  const {
    positions,
    orders,
    indicativeLevels: rawIndicativeLevels,
    moveStop,
    moveTarget,
    refresh: refreshTrading,
  } = useTradingContext();
  const position = positions.find((p) => p.symbol === symbol) ?? null;
  const entry = position ? num(position.avg_entry_price) : null;
  // Levels on the premium axis (option contracts only): bid/ask, the
  // session's high/low and previous close, entry multiples, intrinsic
  // value, plus the premium's own session VWAP.
  const premium = usePremiumLevels(
    contract,
    usingReplayBars ? replay.bars : intraday.bars,
    usingReplayBars ? null : intraday.quote,
    entry,
    option.kind === "intraday",
    usingReplayBars ? (replaySession?.as_of ?? null) : null,
  );
  // First time a premium chart shows, its levels start visible; after that
  // the Levels menu's own choices (persisted like every other indicator)
  // apply.
  useEffect(() => {
    if (!contract) return;
    // Remembered per name, so a level added (or renamed) later gets the
    // same first showing.
    let seeded: string[] = [];
    try {
      const parsed: unknown = JSON.parse(localStorage.getItem(PREMIUM_LEVELS_SEEDED_KEY) ?? "[]");
      // An older build stored a bare flag here; anything but a list of
      // names means "nothing seeded yet".
      seeded = Array.isArray(parsed) ? parsed.filter((n): n is string => typeof n === "string") : [];
    } catch {
      seeded = [];
    }
    const fresh = PREMIUM_LEVEL_NAMES.filter((name) => !seeded.includes(name));
    if (fresh.length === 0) return;
    try {
      localStorage.setItem(PREMIUM_LEVELS_SEEDED_KEY, JSON.stringify([...seeded, ...fresh]));
    } catch {
      return;
    }
    setVisibleIndicators((current) => {
      const next = new Set(current);
      for (const name of fresh) next.add(name);
      persistLevelSet(VISIBLE_INDICATORS_KEY, next);
      return next;
    });
  }, [contract]);
  const exits = position ? exitsForPosition(position, orders) : null;
  const positionSide: "long" | "short" = position?.side === "short" ? "short" : "long";
  // Memoized on the extracted primitives, not on `positions`/`orders`
  // directly: those get new array/object identities on every poll tick even
  // when nothing for this symbol changed, and an unstable identity here would
  // make CandleChart tear down and rebuild its price lines just as often.
  const positionLevels = useMemo<PositionLevels | null>(() => {
    if (!position || entry == null) return null;
    return { side: positionSide, entry, stop: exits?.stopLoss ?? null, target: exits?.takeProfit ?? null };
  }, [position, entry, positionSide, exits?.stopLoss, exits?.takeProfit]);

  // Working orders on the contract shown by the premium chart, as dashed
  // lines at their limits. Equity charts draw their exits via
  // positionLevels instead; a stock's resting entry orders stay off the
  // chart as before.
  const contractOrders = useMemo(
    () => (contract ? orders.filter((o) => o.symbol === symbol) : []),
    [contract, orders, symbol],
  );
  const orderLevelsKey = contractOrders.map((o) => `${o.id}:${o.side}:${o.qty}:${o.limit_price}`).join("|");
  // Premium-trigger bounds on the contract, reported by the ContractTicket
  // (which owns the trigger poll); reset with the symbol.
  const [triggerLevels, setTriggerLevels] = useState<OrderLevel[]>([]);
  useEffect(() => setTriggerLevels([]), [symbol]);
  const orderLevels = useMemo<OrderLevel[]>(
    () => [
      ...contractOrders.flatMap((o) => {
        const price = Number(o.limit_price);
        if (!Number.isFinite(price) || price <= 0) return [];
        const side: "buy" | "sell" = o.side === "sell" ? "sell" : "buy";
        return [{ price, side, title: `${side === "buy" ? "Buy" : "Sell"} ${o.qty ?? ""}`.trim() }];
      }),
      ...triggerLevels,
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [orderLevelsKey, triggerLevels],
  );

  // What the chart actually draws -- each field nulled out when its Levels
  // checkbox is unchecked, independent of whether the position itself has
  // that value.
  const visiblePositionLevels = useMemo<PositionLevels | null>(() => {
    if (!positionLevels) return null;
    return {
      side: positionLevels.side,
      entry: visibleTradeLevels.has("entry") ? positionLevels.entry : null,
      stop: visibleTradeLevels.has("stop") ? positionLevels.stop : null,
      target: visibleTradeLevels.has("target") ? positionLevels.target : null,
    };
  }, [positionLevels, visibleTradeLevels]);

  // The order ticket's own draft entry/stop/target, for the same symbol on
  // screen -- refused (not just filtered) when the symbol doesn't match, so
  // switching charts never leaves a stale draft floating over an unrelated
  // stock while OrderTicket's own effects catch up. Reuses the Levels
  // dropdown's own entry/stop/target checkboxes -- one mental model for
  // "is this level showing," real or draft.
  const visibleIndicativeLevels = useMemo<PositionLevels | null>(() => {
    if (!rawIndicativeLevels || rawIndicativeLevels.symbol !== symbol) return null;
    return {
      side: rawIndicativeLevels.side,
      entry: visibleTradeLevels.has("entry") ? rawIndicativeLevels.entry : null,
      stop: visibleTradeLevels.has("stop") ? rawIndicativeLevels.stop : null,
      target: visibleTradeLevels.has("target") ? rawIndicativeLevels.target : null,
    };
  }, [rawIndicativeLevels, symbol, visibleTradeLevels]);

  // Committed on drop, not memoized: CandleChart reads these through a ref
  // it keeps current every render (see onMovePositionLevelRef there), not
  // through its effect dependency arrays, specifically so a fresh function
  // identity here -- unavoidable anyway, since moveStop/moveTarget are new
  // functions on every render of useTrading() -- doesn't tear down and
  // rebuild the chart's price lines on every poll tick.
  const onMovePositionLevel = symbol
    ? (field: "stop" | "target", price: number) => {
        // A drag is a one-gesture write. Real money asks for the typed
        // confirmation instead -- the Positions tab has it.
        if (tradingMode.mode === "live") {
          setDragError("Live mode: move stops and targets from the Positions tab, with confirmation.");
          return;
        }
        const orderId = field === "stop" ? exits?.stopOrderId : exits?.targetOrderId;
        if (!orderId) return;
        const move = field === "stop" ? moveStop : moveTarget;
        move(orderId, symbol, price).catch((err: unknown) => {
          setDragError(err instanceof Error ? err.message : String(err));
        });
      }
    : undefined;

  // No order to reject here -- just writes the new price into the ticket
  // that published these levels (see IndicativeLevels.onDragStop/
  // onDragTarget). Guarded on the symbol match even though a mismatched
  // draft never has a line to drag in the first place (visibleIndicativeLevels
  // is null then), for the same belt-and-suspenders reason that memo is.
  const onMoveIndicativeLevel =
    rawIndicativeLevels && rawIndicativeLevels.symbol === symbol
      ? (field: "stop" | "target", price: number) => {
          if (field === "stop") rawIndicativeLevels.onDragStop?.(price);
          else rawIndicativeLevels.onDragTarget?.(price);
        }
      : undefined;

  // Stories behind the 📰 pin the user clicked, shown in a popover over the
  // chart -- the Symbol Info widget also highlights them, but in the dock
  // it may be a background tab or closed, so the chart shows them itself.
  const [openNews, setOpenNews] = useState<number[] | null>(null);
  useEffect(() => setOpenNews(null), [symbol]);
  useEffect(() => {
    if (!openNews) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenNews(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [openNews]);
  const onNewsClick = useCallback(
    (times: number[]) => {
      setHighlightedNews(times);
      setOpenNews(times);
    },
    [setHighlightedNews],
  );
  const openStories = useMemo(() => {
    if (!openNews || !symbolInfo.info || symbolInfo.info.symbol !== symbol) return [];
    const wanted = new Set(openNews);
    return symbolInfo.info.news.filter((item) => wanted.has(Math.floor(Date.parse(item.published_at) / 1000)));
  }, [openNews, symbolInfo.info, symbol]);

  // The shared symbol info is the dashboard's selected symbol's; a pinned
  // copy showing something else must not pin that symbol's stories.
  const newsMarkers = useMemo(
    () =>
      symbolInfo.info && symbolInfo.info.symbol === symbol
        ? symbolInfo.info.news
            .map((item) => ({
              time: Math.floor(Date.parse(item.published_at) / 1000),
              headline: item.headline,
            }))
            .filter((item) => Number.isFinite(item.time))
        : [],
    [symbolInfo.info, symbol],
  );

  const displayed = useMemo(() => {
    if (option.kind === "intraday") {
      if (usingReplayBars) {
        // VWAP/indicators come from a separate fetch (useReplayIndicators)
        // rather than replay.bars itself -- see
        // routers/replay.py's /indicators/{symbol}. They're computed at
        // 5-minute resolution (replay's native bar size, not live's
        // 1-minute), so an EMA here reads slower than its live counterpart
        // at the same nominal length; still no historical NBBO quotes, so
        // spread-derived indicators remain unavailable either way.
        return aggregateBars(
          replay.bars,
          contract ? premium.vwap : vwapFromPremarket ? replayIndicators.vwapPremarket : replayIndicators.vwap,
          option.minutes ?? 1,
          contract ? [] : replayIndicators.indicators,
        );
      }
      return aggregateBars(
        intraday.bars,
        // A contract's VWAP is computed here from its bars (the option
        // stream carries none); it has no premarket, so one anchor only.
        contract ? premium.vwap : vwapFromPremarket ? intraday.vwapPremarket : intraday.vwap,
        option.minutes ?? 1,
        intraday.indicators,
      );
    }
    // This timeframe's *own* indicators, not the intraday feed's. Reusing
    // the minute feed's list here meant the backend's per-timeframe gating
    // never reached the chart at all: a weekly view still drew the daily
    // range, because those lines came from the 1Min request rather than
    // from the weekly one that had already dropped them.
    //
    // The "level" filter stays, and is a separate concern: "series"-kind
    // indicators (e.g. an EMA) are minute-resolution -- on an
    // hourly/daily/weekly/monthly chart that's both semantically odd to
    // overlay and, left unaggregated, would trip the same
    // resolution-mismatch zoom bug aggregateBars exists to avoid. "level"
    // lines are flat values, unaffected either way, so only those show here.
    return {
      bars: historical.bars,
      vwap: historical.vwap,
      indicators: historical.indicators.filter((i) => i.kind === "level"),
    };
  }, [
    option,
    usingReplayBars,
    replay.bars,
    replayIndicators.vwap,
    replayIndicators.vwapPremarket,
    replayIndicators.indicators,
    intraday.bars,
    intraday.vwap,
    intraday.vwapPremarket,
    vwapFromPremarket,
    intraday.indicators,
    contract,
    premium.vwap,
    historical.bars,
    historical.vwap,
    historical.indicators,
  ]);

  // gexLevels folded in here, not just at the CandleChart prop, so the
  // Levels-checklist below (which also maps over this same list) can offer
  // it as a toggle -- both call sites have to see one consistent array.
  // EMA of the premium over the displayed (aggregated) bars, so it lines
  // up with the candles at any timeframe.
  const premiumSeries = usePremiumSeries(contract, displayed.bars);
  const indicatorsWithGex = useMemo(() => {
    const extra = [gexLevels, spreadLevels, ...premium.levels, premiumSeries].filter(
      (i): i is NonNullable<typeof i> => i !== null,
    );
    return extra.length > 0 ? [...displayed.indicators, ...extra] : displayed.indicators;
  }, [displayed.indicators, gexLevels, spreadLevels, premium.levels, premiumSeries]);
  // Memoized rather than filtered inline at the prop: CandleChart's
  // indicators effect tears down and rebuilds every price line whenever
  // this reference changes, and this component re-renders on every trade
  // tick -- several times a second -- so an inline filter had it rebuilding
  // the lines that often, with a drag in progress liable to be interrupted
  // by the rebuild.
  const chartIndicators = useMemo(
    () => indicatorsWithGex.filter((i) => visibleIndicators.has(i.name)),
    [indicatorsWithGex, visibleIndicators],
  );

  // Prefers the finer-grained live feed's own latest tick over the
  // (possibly coarser-aggregated) displayed series, same as before replay
  // existed -- except while replaying, where there is no finer-grained
  // source than displayed.bars itself (replay's native resolution is
  // already 5 minutes).
  const lastPrice =
    (!usingReplayBars ? intraday.bars[intraday.bars.length - 1]?.c : undefined) ??
    displayed.bars[displayed.bars.length - 1]?.c ??
    null;

  const activeFeed = option.kind === "intraday" ? (usingReplayBars ? replay : intraday) : historical;
  const noBarsYet =
    option.kind === "intraday" && !activeFeed.loading && activeFeed.bars.length === 0 && !activeFeed.error;
  const noHistoricalData =
    option.kind === "historical" &&
    !historical.loading &&
    historical.bars.length === 0 &&
    !historical.error;

  // Built as a value rather than returned directly, so fullscreen can
  // portal this exact tree to document.body instead of mounting a second
  // copy of it (see the return statement below) -- one CandleChart, one WS
  // subscription, regardless of where in the DOM it currently renders.
  const content = (
    <div
      className={`widget chart-widget${isFullscreen ? " chart-widget-fullscreen" : ""}${dropActive ? " drop-target" : ""}`}
      {...dropProps}
    >
      <div className="widget-header">
        <div className="chart-toolbar">
          {pinned ? (
            <form className="chart-symbol-form" onSubmit={submitSymbol}>
              <input
                type="text"
                value={symbolDraft}
                placeholder="Symbol"
                spellCheck={false}
                autoCapitalize="characters"
                onChange={(e) => setSymbolDraft(e.target.value.toUpperCase())}
                title="This chart is pinned: type a symbol (or an option contract) and press Enter, or drop a row from a scanner. It does not follow the scanner's selection."
              />
              <span className="chart-pinned-badge">pinned</span>
              {contract && symbol && <span className="symbol">{formatLeg(symbol)}</span>}
            </form>
          ) : (
            <span className="symbol" title={contract ? `${symbol} -- option premium` : undefined}>
              {contract && symbol ? formatLeg(symbol) : (symbol ?? "Select a symbol")}
            </span>
          )}
          {contract && (
            <span className="chart-premium-badge" title="This chart shows the option's premium per share; the underlying's levels are not drawn here">
              premium
            </span>
          )}
          {contract && onSelectSymbol && (
            <button
              type="button"
              className="timeframe-button"
              onClick={() => onSelectSymbol(contract.underlying)}
              title={`Back to the ${contract.underlying} stock chart`}
            >
              {contract.underlying} ↗
            </button>
          )}
          {lastPrice != null && <span className="last-price">{formatPrice(lastPrice)}</span>}
          {contract && intraday.quote && (
            <span
              className="chart-quote"
              title={`Bid ${intraday.quote.bid ?? "—"} × ${intraday.quote.bid_size ?? "—"} · Ask ${intraday.quote.ask ?? "—"} × ${intraday.quote.ask_size ?? "—"} (live)`}
            >
              {intraday.quote.bid?.toFixed(2) ?? "—"} / {intraday.quote.ask?.toFixed(2) ?? "—"}
            </span>
          )}
          {usingReplayBars && replaySession && (
            <span
              className="replay-chart-badge"
              title={`Showing replayed bars as of ${new Date(replaySession.as_of).toLocaleString()} -- not live`}
            >
              REPLAY
            </span>
          )}
        </div>
        <div className="chart-toolbar">
          <div className="timeframe-selector" role="group" aria-label="Chart timeframe">
            {TIMEFRAME_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                className="timeframe-button"
                aria-pressed={timeframeKey === opt.key}
                onClick={() => {
                  setTimeframeKey(opt.key);
                  // A focused pick/trade otherwise keeps re-scrolling back to
                  // itself on every live tick once the new timeframe's bars
                  // start arriving (see CandleChart's focus-scroll effect) --
                  // picking a timeframe here is manual control the same way
                  // selectSymbol's own comment already treats picking a
                  // symbol another way, so it lets go of the focus too.
                  onClearFocus?.();
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <div className="timeframe-selector" role="group" aria-label="Chart type">
            {CHART_TYPES.map((type) => (
              <button
                key={type.key}
                type="button"
                className="timeframe-button"
                aria-pressed={chartType === type.key}
                onClick={() => setChartType(type.key)}
                title={type.title}
              >
                {type.label}
              </button>
            ))}
          </div>
          <div className="chart-type-toggle">
            {CURSOR_MODES.map((mode) => (
              <button
                key={mode.key}
                type="button"
                className="timeframe-button"
                aria-pressed={cursorMode === mode.key}
                onClick={() => setCursorMode(mode.key)}
                title={mode.title}
              >
                {mode.label}
              </button>
            ))}
          </div>
          <div className="chart-type-toggle">
            <button
              type="button"
              className="timeframe-button"
              aria-pressed={autoScroll}
              onClick={() => setAutoScroll(!autoScroll)}
              title={
                autoScroll
                  ? "Auto-scroll on: every new candle brings the chart back to the newest bar. Click to switch off and scroll freely."
                  : "Auto-scroll off: the chart stays wherever you drag it. Click to jump back to the newest bar and follow it again."
              }
            >
              Auto
            </button>
          </div>
          {option.kind === "intraday" && (
            <button
              type="button"
              className="vwap-legend"
              aria-pressed={!contract && vwapFromPremarket}
              onClick={() => {
                if (!contract) setVwapFromPremarket((v) => !v);
              }}
              title={
                contract
                  ? "Session VWAP of the premium, from the contract's own bars (options have no premarket)."
                  : vwapFromPremarket
                    ? "VWAP anchored at the premarket open, counting every print (what TradingView draws). Click for the 09:30 session anchor."
                    : "VWAP anchored at the 09:30 session open, premarket excluded. Click to anchor at the premarket open instead."
              }
            >
              <span className="vwap-swatch" /> VWAP {!contract && vwapFromPremarket ? "(pre)" : "(session)"}
            </button>
          )}
          {gexReading && (
            <span
              className="gex-net-badge"
              style={{ color: gexReading.net_gex >= 0 ? palette.up : palette.down }}
              title="Net dealer gamma exposure -- see the GEX Plan widget for what this regime tends to mean"
            >
              Net GEX {gexReading.net_gex >= 0 ? "+" : "-"}${(Math.abs(gexReading.net_gex) / 1e9).toFixed(2)}B
            </span>
          )}
          <div className="levels-dropdown" ref={levelsButtonRef}>
            <button
              type="button"
              className="timeframe-button"
              aria-expanded={levelsMenuOpen}
              onClick={() => setLevelsMenuOpen((v) => !v)}
              title="Choose which indicator and trade levels are drawn on the chart"
            >
              Levels ▾
            </button>
          </div>
          {levelsMenuOpen &&
            levelsMenuPos &&
            createPortal(
              // Portalled to <body> rather than positioned relative to
              // .levels-dropdown: react-grid-layout positions every widget
              // with a CSS transform, which makes the widget the containing
              // block for anything positioned inside it, and .widget itself
              // clips overflow -- so an absolutely-positioned menu renders
              // but gets cut off at the widget's own edge (see Modal.tsx,
              // which hit the identical bug).
              <div
                className="levels-menu"
                role="menu"
                ref={levelsMenuRef}
                style={{ top: levelsMenuPos.top, left: levelsMenuPos.left }}
              >
                {indicatorsWithGex.length === 0 && !position && !visibleIndicativeLevels && (
                  <div className="levels-menu-empty">No levels available</div>
                )}
                {indicatorsWithGex.map((indicator) => (
                  <label
                    key={indicator.name}
                    className={indicator.error ? "levels-menu-item failed" : "levels-menu-item"}
                    title={indicator.error ? `${indicator.name} failed: ${indicator.error}` : undefined}
                  >
                    <input
                      type="checkbox"
                      checked={!indicator.error && visibleIndicators.has(indicator.name)}
                      disabled={!!indicator.error}
                      onChange={() => toggleIndicatorVisible(indicator.name)}
                    />
                    <span
                      className="indicator-swatch"
                      style={{
                        background: indicator.error
                          ? "#d1242f"
                          : Object.values(indicator.colors ?? {})[0] ?? "#888",
                      }}
                    />
                    {indicator.error ? `${indicator.name} ✕` : indicator.name}
                  </label>
                ))}
                {(position || visibleIndicativeLevels) && (
                  <>
                    <div className="levels-menu-divider" />
                    {tradeLevelItems().map((item) => (
                      <label key={item.key} className="levels-menu-item">
                        <input
                          type="checkbox"
                          checked={visibleTradeLevels.has(item.key)}
                          onChange={() => toggleTradeLevel(item.key)}
                        />
                        <span className="indicator-swatch" style={{ background: item.color }} />
                        {item.label}
                      </label>
                    ))}
                  </>
                )}
              </div>,
              document.body,
            )}
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={isFullscreen}
            onClick={() => setIsFullscreen((v) => !v)}
            title={isFullscreen ? "Exit fullscreen (Esc)" : "Fullscreen (Esc to exit)"}
          >
            {isFullscreen ? "⤡" : "⤢"}
          </button>
        </div>
      </div>
      {dragError && (
        <div className="order-rejection" role="status">
          {dragError}{" "}
          <button type="button" className="row-action" onClick={() => setDragError(null)}>
            Dismiss
          </button>
        </div>
      )}
      {contract && symbol && (
        <ContractTicket
          symbol={symbol}
          contract={contract}
          mode={tradingMode.mode}
          lastPrice={lastPrice}
          position={position}
          orders={contractOrders}
          onSubmitted={refreshTrading}
          onTriggerLevels={setTriggerLevels}
        />
      )}
      <div className="widget-body">
        {!symbol ? (
          <div className="widget-empty">
            {pinned ? "Type a symbol above, or drop one from a scanner or the watchlist." : "Click a symbol in a scanner to load its chart."}
          </div>
        ) : activeFeed.error ? (
          <div className="widget-error">{activeFeed.error}</div>
        ) : activeFeed.loading && displayed.bars.length === 0 ? (
          <div className="widget-empty">Loading {symbol}…</div>
        ) : noBarsYet ? (
          <div className="widget-empty">
            {usingReplayBars
              ? `No bars for ${symbol} yet at this point in the replay.`
              : `No trades printed for ${symbol} yet today. Premarket volume is thin — this fills in once trades start (most reliably at 9:30 ET open).`}
          </div>
        ) : noHistoricalData ? (
          <div className="widget-empty">No {option.label} history available for {symbol}.</div>
        ) : (
          <CandleChart
            bars={displayed.bars}
            chartType={chartType}
            vwap={displayed.vwap}
            indicators={chartIndicators}
            positionLevels={visiblePositionLevels}
            indicativeLevels={visibleIndicativeLevels}
            orderLevels={orderLevels}
            onMovePositionLevel={onMovePositionLevel}
            onMoveIndicativeLevel={onMoveIndicativeLevel}
            cursorMode={cursorMode}
            autoScroll={autoScroll}
            // Session tinting only where a bar sits inside one session --
            // on daily and coarser charts the distinction does not exist.
            shadeSessions={option.kind === "intraday" && appSettings.sessionShading}
            palette={palette}
            hollowCandles={hollow}
            dark={dark}
            locale={numberLocale}
            // Only honour the focus while it still refers to the symbol on
            // screen; a stale one would drag the chart to an unrelated time
            // after the user clicks a different row.
            focusTime={focus && focus.symbol === symbol ? focus.time : null}
            focusTrade={focus && focus.symbol === symbol ? (focus.trade ?? null) : null}
            // News markers only where a bar is finer than a session: the
            // list covers the last few days, so on daily+ charts every
            // story would pile onto the newest one or two candles.
            news={option.kind === "intraday" ? newsMarkers : []}
            onNewsClick={onNewsClick}
          />
        )}
        {openStories.length > 0 && (
          <div className="chart-news-popover" role="dialog" aria-label="News at this bar">
            <div className="chart-news-popover-header">
              <strong>
                {openStories.length === 1 ? "News" : `${openStories.length} stories`} ·{" "}
                {new Date(openStories[0].published_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </strong>
              <button type="button" className="row-action" onClick={() => setOpenNews(null)} title="Close (Esc)">
                ✕
              </button>
            </div>
            {openStories.map((item) => (
              <div key={`${item.published_at}:${item.headline}`} className="chart-news-story">
                {item.url ? (
                  <a href={item.url} target="_blank" rel="noreferrer" className="symbol-news-headline">
                    {item.headline}
                  </a>
                ) : (
                  <span className="symbol-news-headline">{item.headline}</span>
                )}
                <span className="symbol-news-source">
                  — {item.source} · {newsAge(item.published_at)}
                </span>
                {item.summary && <p className="chart-news-summary">{item.summary}</p>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );

  return isFullscreen
    ? createPortal(<div className="chart-fullscreen-backdrop">{content}</div>, document.body)
    : content;
}
