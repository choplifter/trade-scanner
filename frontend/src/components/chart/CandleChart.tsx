import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type LineData,
  type IRange,
  type Time,
  type WhitespaceData,
  type UTCTimestamp,
} from "lightweight-charts";

import { isMarkerSeries, isPointSeries } from "../../types/alpaca";
import type { Bar, IndicatorResult, IndicatorStyle } from "../../types/alpaca";
import { crosshairTimeFormatter, tickMarkFormatter } from "../../utils/chartTime";
import { SESSION_FILLS, sessionBandData, sessionBandOptions } from "./sessionBands";

/** Candles carry open/high/low; a line carries only the close. Both read the
 * same bars -- the line is for seeing the shape of a move without the wicks,
 * which on a thin premarket tape is mostly noise. */
export type ChartType = "candles" | "line";

/** What the mouse does over the chart.
 *
 * "pointer" hides the crosshair entirely -- an ordinary arrow, for when the
 * lines are in the way. "crosshair" follows the mouse freely. "magnet" snaps
 * the horizontal line to the nearest of the bar's open/high/low/close, which
 * is the one that answers "is this level actually where that wick ended" --
 * reading a price off a free crosshair is guesswork at a few pixels per cent.
 *
 * Not an indicator, and could not be: an indicator is a backend file that
 * returns numbers to draw. This is a property of the chart, not of the data. */
export type CursorMode = "pointer" | "crosshair" | "magnet";

const CROSSHAIR_MODES: Record<CursorMode, CrosshairMode> = {
  pointer: CrosshairMode.Hidden,
  crosshair: CrosshairMode.Normal,
  magnet: CrosshairMode.MagnetOHLC,
};

interface CandleChartProps {
  bars: Bar[];
  chartType: ChartType;
  vwap: (number | null)[];
  /** Already filtered down to whatever the Levels dropdown has checked --
   * this component draws exactly what it's given, it doesn't decide what's
   * on. */
  indicators: IndicatorResult[];
  /** Entry/stop/target for the position open on the symbol on screen, if
   * any. Null when there is none; an individual field is null when the
   * Levels dropdown has that one unchecked. */
  positionLevels: PositionLevels | null;
  /** Entry/stop/target from the order ticket currently being built for the
   * symbol on screen -- not a real position, drawn dashed (see
   * INDICATIVE_LINE_STYLE) specifically so it can never be mistaken for one.
   * Same per-field nulling convention as positionLevels. Independent of it:
   * a real position and a draft ticket for the same symbol can both have
   * values, and both draw -- e.g. planning a scale-in the levels line
   * shows next to the position already protecting it. */
  indicativeLevels: PositionLevels | null;
  /** Dragging a real position's Stop or Target line to a new price calls
   * this with the field and the price it was dropped at -- the caller is
   * responsible for actually moving the order (and for what happens if that
   * fails; this component only draws whatever positionLevels says next,
   * which is how a rejected move visibly snaps back). Undefined, or a field
   * whose line has nothing to move (see the position-lines effect), means
   * that line isn't offered as draggable at all -- entry is never included,
   * it's a fill price, not an order. */
  onMovePositionLevel?: (field: "stop" | "target", price: number) => void;
  /** Same as onMovePositionLevel, for the order ticket's draft Stop/Target
   * lines -- there's no order to move here, so the caller just writes the
   * new price back into the ticket's own input (see IndicativeLevels'
   * onDragStop/onDragTarget, which is what ChartWidget wires this to). */
  onMoveIndicativeLevel?: (field: "stop" | "target", price: number) => void;
  cursorMode: CursorMode;
  /** Unix seconds to scroll into view — a backtest pick's entry time, or a
   * journal trade's entry time when focusTrade is set. Null leaves the
   * chart wherever the user left it. */
  focusTime?: number | null;
  /** Set alongside focusTime when the focus is a closed trade
   * (TradeJournalWidget), not a backtest pick -- draws "Entry" (at
   * focusTime) and "Exit" arrows spanning the trade, and scrolls to show
   * the whole trade rather than padding around one point. Null/undefined
   * means focusTime (if any) is a plain backtest pick, drawn as "Pick". */
  focusTrade?: { exitTime: number; won: boolean } | null;
  /** Recent headlines to pin on the timeline, each at the bar nearest its
   * publish time. Same items the info panel lists — the marker answers
   * "when", the panel answers "what". */
  news?: { time: number; headline: string }[];
  /** A bar carrying a 📰 pin was clicked. Receives the publish times of
   * the stories pinned there — the id the info panel can find them by. */
  onNewsClick?: (newsTimes: number[]) => void;
  /** Tint premarket/after-hours bars TradingView-style. Off on daily and
   * coarser charts, where one bar spans whole sessions and the tint would
   * describe nothing. */
  shadeSessions?: boolean;
}

/** How many bars of history/future to show either side of a focused pick
 * (or a journal trade's entry/exit) -- bar count rather than a fixed time
 * span so it means the same thing on any timeframe: wide enough to see what
 * led into the entry and what happened after, narrow enough that the bar in
 * question is still identifiable. */
const FOCUS_PADDING_BARS = 12;

/** Same pin the Dash backtest page drops on a clicked pick (see
 * dash_app/assets/lightweight_chart.html's markPick), so the two surfaces
 * mark an entry identically. */
const PICK_MARKER_COLOR = "#2a78d6";

/** Entry/stop/target for whatever position is open on the symbol on screen.
 * Each field is independently nullable -- the caller (ChartWidget) nulls out
 * whichever of the three the Levels dropdown has unchecked, so this is drawn
 * unconditionally: by the time it gets here, null already means "hidden." */
export interface PositionLevels {
  side: "long" | "short";
  entry: number | null;
  stop: number | null;
  target: number | null;
}

export const POSITION_ENTRY_COLOR = "#5b8bd6";
// Same green/red as the candlestick series' up/down colors below -- a
// position's stop and target reuse the chart's own favorable/unfavorable
// semantic rather than introducing a second palette.
export const POSITION_TARGET_COLOR = "#0ca30c";
export const POSITION_STOP_COLOR = "#d03b3b";
// Solid and a shade wider than a "level" indicator's default (width 1,
// dashed) so a position line reads as distinct at a glance.
const POSITION_LINE_WIDTH = 2 as const;
// Indicative (draft-ticket) lines reuse positionLevels' own colors -- same
// meaning, just not real yet -- but stay dashed at all times, unlike a real
// position's always-solid lines, so the two can never read as the same
// thing even at a glance.
const INDICATIVE_LINE_WIDTH = 2 as const;
const INDICATIVE_LINE_STYLE = LineStyle.Dashed;

/** The bar closest to `time`, since a pick's entry rarely lands exactly on a
 * bar boundary of whatever timeframe is being displayed -- a 5-minute entry
 * viewed on a daily chart has no exact match at all. */
function nearestBarTime(bars: Bar[], time: number): UTCTimestamp | null {
  let nearest: UTCTimestamp | null = null;
  let bestDiff = Infinity;
  for (const bar of bars) {
    const barTime = toUnixSeconds(bar.t);
    const diff = Math.abs(barTime - time);
    if (diff < bestDiff) {
      bestDiff = diff;
      nearest = barTime;
    }
  }
  return nearest;
}

/** The index of the bar closest to `time` -- for setVisibleLogicalRange,
 * which (unlike setVisibleRange) is the mechanism this file's default view
 * and resize handling already rely on. A time-based setVisibleRange call
 * was tried first for the focus-scroll effect below and found to be
 * silently overridden back to the default right-anchored view on this
 * chart (confirmed live: the exact requested {from, to} read back
 * unchanged as the *default* range immediately after the call, with
 * shiftVisibleRangeOnNewBar -- a chart default this file doesn't turn off
 * -- the likely cause). Logical/index-based ranging doesn't hit that path. */
function nearestBarIndex(bars: Bar[], time: number): number | null {
  let nearest: number | null = null;
  let bestDiff = Infinity;
  for (let i = 0; i < bars.length; i++) {
    const diff = Math.abs(toUnixSeconds(bars[i].t) - time);
    if (diff < bestDiff) {
      bestDiff = diff;
      nearest = i;
    }
  }
  return nearest;
}

function toUnixSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function barToCandle(bar: Bar): CandlestickData {
  return { time: toUnixSeconds(bar.t), open: bar.o, high: bar.h, low: bar.l, close: bar.c };
}

function barToClose(bar: Bar): LineData {
  return { time: toUnixSeconds(bar.t), value: bar.c };
}

function barToVolume(bar: Bar): HistogramData {
  const up = bar.c >= bar.o;
  return {
    time: toUnixSeconds(bar.t),
    value: bar.v,
    color: up ? "rgba(12,163,12,0.5)" : "rgba(208,59,59,0.5)",
  };
}

const MIN_SPACING = 3;
const MAX_SPACING = 10;

// Empty chart kept to the right of the newest bar while level lines are
// shown, so their labels have somewhere to sit that is not on top of the
// candles.
//
// Two earlier attempts are worth recording, because both looked plausible:
// padding the visible logical range did nothing (setVisibleLogicalRange
// clamps `to` at the last bar), and widening the price scale via
// minimumWidth shrank the whole pane, which moved the labels left along with
// the chart and so did not separate them. rightOffsetPixels scrolls the
// content within a pane of unchanged width, which is the thing actually
// wanted.
const LEVEL_LABEL_CLEARANCE_PX = 150;

// Every place that repositions the viewport has to re-assert the margin,
// because setting a visible range repositions the content and drops it.
// That is what made the first attempts look like the option did nothing: it
// was applied, then immediately cancelled by the next range set -- including
// the one the indicators effect does itself, restoring the range it saved
// before adding the lines.
function applyLabelClearance(chart: IChartApi, needsClearance: boolean) {
  chart.timeScale().applyOptions({
    rightOffsetPixels: needsClearance ? LEVEL_LABEL_CLEARANCE_PX : 0,
  });
}

function hasAnyLevel(levels: PositionLevels | null): boolean {
  return levels != null && (levels.entry != null || levels.stop != null || levels.target != null);
}

/** Whether there's anything the Levels dropdown has actually checked --
 * indicators is already pre-filtered by the caller, and positionLevels'/
 * indicativeLevels' fields are individually nulled out when unchecked, so
 * this is the one place that needs to look at all three to decide if label
 * space is worth reserving. */
function hasAnyVisibleLevel(
  indicators: IndicatorResult[],
  positionLevels: PositionLevels | null,
  indicativeLevels: PositionLevels | null,
): boolean {
  return indicators.length > 0 || hasAnyLevel(positionLevels) || hasAnyLevel(indicativeLevels);
}

// Show as many bars as fit at a readable spacing: fit everything when there's
// little data (capped at MAX_SPACING so a handful of bars doesn't stretch into
// oversized candles), or the most recent bars at MIN_SPACING when there's more
// data than the pane can show at once. Shared by the data effect and the
// resize handler so the two can't drift apart -- the pane's width is an input
// either way, so a resize has to recompute this just as a new bar does.
function visibleLogicalRange(width: number, barCount: number) {
  if (barCount <= 0 || width <= 0) return null;
  const spacing = Math.min(MAX_SPACING, Math.max(MIN_SPACING, width / barCount));
  const visibleCount = Math.min(barCount, Math.max(1, Math.ceil(width / spacing)));
  return { from: barCount - visibleCount, to: barCount - 1 };
}

/** One entry per currently-draggable Stop/Target line -- see
 * draggableLinesRef for how this list is kept current. */
interface DraggableLine {
  line: IPriceLine;
  source: "position" | "indicative";
  field: "stop" | "target";
}

// Pixel tolerance for "the cursor is on this line" -- generous enough to
// grab a thin line without pixel-perfect aim, tight enough not to swallow
// clicks meant for the chart just below or above it.
const DRAG_HIT_TOLERANCE_PX = 6;

/** Which draggable line, if any, sits under a given pane-local y coordinate
 * right now -- reads each line's *current* price via IPriceLine.options()
 * rather than a value cached at creation time, so this stays correct as a
 * line's price changes (including mid-drag, when the line being dragged is
 * itself moving). */
function draggableLineAt(
  y: number,
  priceSeries: ISeriesApi<"Candlestick"> | ISeriesApi<"Line">,
  lines: DraggableLine[],
): DraggableLine | null {
  for (const entry of lines) {
    // A disposed line (its effect removed it a moment ago but hasn't
    // rebuilt draggableLinesRef yet) throws on .options() rather than
    // returning something -- skip it instead of breaking hit-testing, here
    // on every mousemove, for every other line in the list too.
    try {
      const lineY = priceSeries.priceToCoordinate(entry.line.options().price);
      if (lineY != null && Math.abs(lineY - y) <= DRAG_HIT_TOLERANCE_PX) return entry;
    } catch {
      continue;
    }
  }
  return null;
}

// Chart instance is created once and mutated imperatively via the
// lightweight-charts API rather than re-rendered through React, since it
// owns its own canvas and re-creating it per tick would be far too slow for
// live data.
/** A nullable series as line points, with gaps kept as gaps.
 *
 * lightweight-charts joins consecutive *data* points with a straight line, so
 * dropping the nulls does not leave a hole -- it welds the two sides
 * together. On a session-anchored series that is badly wrong: VWAP is null
 * through premarket and restarts each morning, so filtering produced a
 * diagonal running from one session's closing VWAP straight up to the next
 * session's, drawn across the gap as though it were a price move. On IPST,
 * which went from $2 to $8 overnight, that diagonal was the most prominent
 * line on the chart and described nothing.
 *
 * A whitespace point (time, no value) reserves the slot and breaks the line.
 */
function toLinePoints<T>(
  items: T[],
  time: (item: T, index: number) => UTCTimestamp,
  value: (item: T, index: number) => number | null | undefined,
): (LineData | WhitespaceData)[] {
  return items.map((item, i) => {
    const v = value(item, i);
    return v == null ? { time: time(item, i) } : { time: time(item, i), value: v };
  });
}


/** The dash patterns an indicator may name, mapped to the library's enum.
 * Kept as names on the wire so an indicator file declares intent ("dotted")
 * rather than a number that only means something inside lightweight-charts. */
const DASH_PATTERNS: Record<string, LineStyle> = {
  solid: LineStyle.Solid,
  dotted: LineStyle.Dotted,
  dashed: LineStyle.Dashed,
  "large-dashed": LineStyle.LargeDashed,
  "sparse-dotted": LineStyle.SparseDotted,
};

/** Defaults per indicator kind, chosen to reproduce exactly what every
 * indicator looked like before STYLE existed -- so a file that declares
 * nothing renders unchanged. */
const DEFAULT_LEVEL_STYLE = { width: 1, dash: LineStyle.Dashed };
const DEFAULT_SERIES_STYLE = { width: 2, dash: LineStyle.Solid };

function resolveStyle(style: IndicatorStyle | undefined, defaults: { width: number; dash: LineStyle }) {
  return {
    width: style?.width ?? defaults.width,
    // An unrecognised name falls back rather than throwing: a new pattern
    // added backend-side should degrade to the default line, not blank the
    // whole indicator.
    dash: (style?.dash && DASH_PATTERNS[style.dash]) ?? defaults.dash,
  };
}

export function CandleChart({
  bars,
  chartType,
  vwap,
  indicators,
  positionLevels,
  indicativeLevels,
  onMovePositionLevel,
  onMoveIndicativeLevel,
  cursorMode,
  focusTime,
  focusTrade,
  news = [],
  onNewsClick,
  shadeSessions = false,
}: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  // Whichever series is currently drawing price. Held as one ref rather than
  // two because everything else on the chart -- the markers primitive, the
  // indicator price lines -- attaches to "the price series" and should not
  // care which shape it is.
  const priceSeriesRef = useRef<ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null>(null);
  // Viewport captured just before a candle/line swap, for the data effect to
  // put back. Held in a ref rather than restored on the spot because the new
  // series has no data yet at that point.
  const pendingRangeRef = useRef<IRange<Time> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  // The premarket/after-hours background washes -- see sessionBands.ts.
  const sessionBandsRef = useRef<{ pre: ISeriesApi<"Histogram">; post: ISeriesApi<"Histogram"> } | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const positionLinesRef = useRef<IPriceLine[]>([]);
  const indicativeLinesRef = useRef<IPriceLine[]>([]);
  const indicatorSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  // The markers primitive, kept so the pick pin is updated in place rather
  // than layered again on every focus change.
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  // Read by the resize handler, which is subscribed once at mount and so
  // can't close over the current bars. A ref rather than a dep of the mount
  // effect, which would rebuild the whole chart on every tick.
  const barCountRef = useRef(0);
  // The resize handler is built once in the mount effect and can't close
  // over per-render props.
  const positionLevelsRef = useRef(positionLevels);
  positionLevelsRef.current = positionLevels;
  // Same reason as positionLevelsRef -- read by the resize handler.
  const hasLevelsRef = useRef(false);
  hasLevelsRef.current = hasAnyVisibleLevel(indicators, positionLevels, indicativeLevels);
  // Applying a range can itself change the time scale's width (different
  // visible bars -> different price labels -> a wider/narrower price scale),
  // which fires subscribeSizeChange again. This swallows that echo.
  const applyingRangeRef = useRef(false);
  // Pending setTimeout id from the focus-scroll effect's reassert loop (see
  // that effect) -- cancelled and restarted on every run so a stale chain
  // from a previous focus (an old trade/pick, or the same one before bars
  // changed again) can't fire a late setVisibleLogicalRange for a viewport
  // that's no longer current.
  const reassertTimeoutRef = useRef<number | null>(null);
  // The logical range the focus-scroll effect below last asked for, or null
  // when no focus is active. Read by the resize handler, which is built once
  // at mount and (like every other resize-handler ref here) can't close over
  // per-render props -- without this, a live tick's own layout reflow (the
  // price/order-ticket panel's numbers changing width, nudging the chart
  // container by a pixel) fires subscribeSizeChange, and the handler's
  // unconditional default-range computation stomped a focused pick/trade
  // right back to the unfocused right-anchored view, over and over as long
  // as the symbol kept ticking -- the flicker this ref exists to stop.
  const focusRangeRef = useRef<{ from: number; to: number } | null>(null);
  // The container width the resize handler last acted on. The same
  // labels-resize-the-axis mechanism fires on the user's own horizontal
  // scroll (new visible bars -> new price labels -> the axis gains or loses
  // a few pixels), and without this check the handler read that as a pane
  // resize and stomped the scrolled-to viewport back to the default
  // right-anchored window -- the "zoom snaps while panning" bug. A real
  // pane resize changes the *container's* width; axis jitter does not.
  const lastContainerWidthRef = useRef(0);
  // First bar's time as of the last data effect run -- how an append (live
  // tick) is told apart from a replacement (symbol/timeframe change). See
  // the data effect for what each means for viewport ownership.
  const firstBarTimeRef = useRef<number | null>(null);
  // Bar time -> publish times of the stories pinned there. Written by the
  // markers effect, read by the click handler -- which is subscribed once
  // at mount and cannot close over per-render props, the same reason
  // barCountRef exists.
  const newsPinsRef = useRef<Map<number, number[]>>(new Map());
  const onNewsClickRef = useRef(onNewsClick);
  onNewsClickRef.current = onNewsClick;
  // Read by the mousedown/mousemove/mouseup handlers below, which are
  // subscribed once in the mount effect and can't close over per-render
  // props -- same reason onNewsClickRef exists.
  const onMovePositionLevelRef = useRef(onMovePositionLevel);
  onMovePositionLevelRef.current = onMovePositionLevel;
  const onMoveIndicativeLevelRef = useRef(onMoveIndicativeLevel);
  onMoveIndicativeLevelRef.current = onMoveIndicativeLevel;
  // Which of the currently-drawn Stop/Target lines can be dragged, rebuilt
  // by the position-lines and indicative-lines effects below whenever they
  // rebuild their own lines -- each effect only touches its own `source`
  // entries here, since the two run independently of each other. Read by
  // the drag handlers to hit-test a mousedown/hover against every
  // draggable line's *current* on-screen position, not just the ones that
  // existed when the listener was attached.
  const draggableLinesRef = useRef<DraggableLine[]>([]);
  // The line currently being dragged, and the price it started at (to tell
  // a real drag apart from a click that never moved -- see the mouseup
  // handler). Null when nothing is being dragged.
  const draggingRef = useRef<(DraggableLine & { startPrice: number }) | null>(null);

  // Re-enables the chart's own pan/zoom after a drag -- see handleMouseDown
  // for why it's disabled at all. Always restores to the enabled shape
  // outright, rather than snapshotting whatever handleScroll/handleScale
  // were right before the drag and restoring *that*: this app never sets
  // them to anything else, so the snapshot was never protecting against a
  // real customization, and it made a stuck-disabled state self-
  // perpetuating -- if a drag anywhere ever ended without its restore
  // running (a mouseup lightweight-charts itself swallowed, a dev-tools
  // interruption, anything), the *next* drag would snapshot that already-
  // disabled state as "correct" and restore back to disabled again,
  // forever. Hardcoding the target means every drag heals it instead.
  function reenableChartInteraction() {
    chartRef.current?.applyOptions({ handleScroll: true, handleScale: true });
  }

  // Aborts an in-progress drag on `source`'s lines -- called by the
  // position-lines/indicative-lines effects just before they dispose and
  // recreate their own lines, so draggingRef is never left pointing at a
  // now-disposed IPriceLine. Left unguarded, an edit landing mid-drag (e.g.
  // typing a new Limit/Target price, whose async preview republishes
  // indicativeLevels a moment later and rebuilds every indicative line,
  // including whichever one is being dragged right then) leaves the next
  // mousemove calling .applyOptions() on a disposed line, which
  // lightweight-charts throws for -- on every remaining mousemove of the
  // drag, which is frequent enough to read as the chart freezing.
  function cancelActiveDrag(source: "position" | "indicative") {
    if (draggingRef.current?.source !== source) return;
    draggingRef.current = null;
    reenableChartInteraction();
    if (containerRef.current) containerRef.current.style.cursor = "";
  }

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const textColor =
      getComputedStyle(document.body).getPropertyValue("--text-secondary").trim() || "#888888";
    const gridColor =
      getComputedStyle(document.body).getPropertyValue("--gridline").trim() || "#2c2c2a";

    const chart = createChart(container, {
      layout: { background: { color: "transparent" }, textColor },
      grid: {
        vertLines: { color: gridColor },
        horzLines: { color: gridColor },
      },
      // Bar spacing is computed and applied explicitly on every data change
      // (see below) instead of left to fitContent()/scrollToRealTime(),
      // which both have failure modes: fitContent() stretches a handful of
      // bars to fill the whole pane (huge candles), while a fixed spacing
      // always shows the same candle count regardless of how much history
      // is loaded (chart looks equally "empty" whether 150 or 3000 bars are
      // available). minBarSpacing still bounds how far a user can manually
      // zoom in.
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        minBarSpacing: 3,
        tickMarkFormatter,
        // Off: the library's own default here unconditionally snaps the
        // view to the newest bar on every live tick, wherever the viewer
        // currently is -- fighting both a focused pick/trade (whose whole
        // point is to stay put on a historical window while new bars keep
        // arriving elsewhere) and this file's own hand-rolled "follow only
        // if already at the right edge" logic a few lines down (the
        // atRightEdge check), which is the one actual mechanism meant to
        // own that decision.
        shiftVisibleRangeOnNewBar: false,
      },
      localization: { timeFormatter: crosshairTimeFormatter },
      autoSize: true,
    });

    // First series in, so every price-bearing series added later -- volume,
    // VWAP, the candles from their own effect -- draws over the washes.
    const sessionBands = {
      pre: chart.addSeries(HistogramSeries, sessionBandOptions(SESSION_FILLS.pre)),
      post: chart.addSeries(HistogramSeries, sessionBandOptions(SESSION_FILLS.post)),
    };
    // Zero margins on the bands' own overlay scale: with the fixed 0..1
    // range from their options, value 1 is the pane's top edge and the fill
    // reaches the bottom -- a full-height wash rather than a half-pane one.
    sessionBands.pre.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 } });

    // The price series itself is created by its own effect below, so
    // switching between candles and a line swaps one series instead of
    // tearing down the chart (which would lose the user's zoom on every
    // toggle).
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } });

    // VWAP overlays the same right-hand price scale as the candles -- it
    // is the day-trading reference line, not a secondary measure.
    const vwapSeries = chart.addSeries(LineSeries, {
      color: "#eda100",
      lineWidth: 2,
      crosshairMarkerVisible: false,
      lastValueVisible: false,
    });

    chartRef.current = chart;
    volumeSeriesRef.current = volumeSeries;
    vwapSeriesRef.current = vwapSeries;
    sessionBandsRef.current = sessionBands;

    // autoSize above already has lightweight-charts observing the container,
    // so hook its own size event rather than adding a second ResizeObserver
    // on the same element -- see .heatmap-container in styles.css for what a
    // redundant observer feeding back into a layout can cost. Without this,
    // resizing the pane keeps the old logical range and just stretches the
    // same bars past MAX_SPACING; a live tick would fix it eventually, but
    // outside market hours nothing re-runs the data effect at all.
    const handleSizeChange = () => {
      if (applyingRangeRef.current) return;
      const container = containerRef.current;
      if (!container) return;
      // Only a real pane resize -- splitter drag, window resize -- changes
      // the container's width. The time scale also reports size changes
      // when the price axis re-labels itself during a horizontal scroll;
      // acting on those reset the user's viewport mid-pan (see
      // lastContainerWidthRef above).
      const width = container.clientWidth || 1;
      if (width === lastContainerWidthRef.current) return;
      lastContainerWidthRef.current = width;
      // A focused pick/trade owns the viewport the same way it does in the
      // data effect above -- recompute its own window rather than the
      // default one, so a resize (real or reflow-triggered) can't undo it.
      const range = focusRangeRef.current ?? visibleLogicalRange(width, barCountRef.current);
      if (!range) return;
      applyingRangeRef.current = true;
      chart.timeScale().setVisibleLogicalRange(range);
      applyLabelClearance(chart, hasLevelsRef.current);
      requestAnimationFrame(() => {
        applyingRangeRef.current = false;
      });
    };
    lastContainerWidthRef.current = container.clientWidth || 1;
    chart.timeScale().subscribeSizeChange(handleSizeChange);

    // A click on a bar carrying a 📰 pin surfaces its stories (the info
    // panel scrolls to and highlights them). Bar-level rather than
    // glyph-level on purpose: the markers plugin has no hit-testing of its
    // own, and the whole column is a far easier click target than an emoji.
    const handleClick = (param: { time?: Time }) => {
      if (param.time == null) return;
      const hit = newsPinsRef.current.get(param.time as number);
      if (hit && hit.length > 0) onNewsClickRef.current?.(hit);
    };
    chart.subscribeClick(handleClick);

    // Dragging a Stop/Target line. lightweight-charts has no price-line drag
    // API, so this is native mouse events on the container, hit-testing
    // against draggableLinesRef (kept current by the position-lines and
    // indicative-lines effects further down).
    const handleMouseMove = (event: MouseEvent) => {
      const priceSeries = priceSeriesRef.current;
      if (!priceSeries) return;
      const rect = container.getBoundingClientRect();
      const y = event.clientY - rect.top;

      const dragging = draggingRef.current;
      if (dragging) {
        const price = priceSeries.coordinateToPrice(y);
        // `!= null` alone isn't enough: NaN is neither null nor undefined,
        // so a NaN result -- coordinateToPrice can return one while the
        // price scale is mid-recompute, which is exactly what's happening
        // right as indicativeLevels/positionLevels changes and the
        // line-drawing effects tear down and rebuild every line -- would
        // otherwise sail through and get handed to a real chart price line.
        // A price line pinned to NaN (or a wild, non-positive value from an
        // out-of-bounds coordinate) can send the library's own axis/scale
        // math into a pathological loop with nothing to throw or catch,
        // which reads as the chart hanging with no console error at all.
        if (price != null && Number.isFinite(price) && price > 0) {
          try {
            dragging.line.applyOptions({ price });
          } catch {
            // The line was disposed out from under this drag -- cancelActiveDrag
            // is meant to catch that before it happens (see its own comment),
            // this is only a backstop against a case it doesn't. Ending the
            // drag cleanly beats leaving every remaining mousemove throwing.
            cancelActiveDrag(dragging.source);
          }
        }
        return;
      }

      // Not dragging: just update the cursor affordance for whatever's
      // under the pointer. Set directly on the element rather than through
      // the cursor-${cursorMode} class (see the component's return value) --
      // that class still owns every other pixel of the chart, this is only
      // ever a hover-local override.
      const hit = draggableLineAt(y, priceSeries, draggableLinesRef.current);
      container.style.cursor = hit ? "ns-resize" : "";
    };

    const handleMouseDown = (event: MouseEvent) => {
      const priceSeries = priceSeriesRef.current;
      if (!priceSeries) return;
      const rect = container.getBoundingClientRect();
      const y = event.clientY - rect.top;
      const hit = draggableLineAt(y, priceSeries, draggableLinesRef.current);
      if (!hit) return;
      // Keeps this from also being read as the start of a chart pan, and
      // the eventual mouseup from being read as a click on whatever bar it
      // lands over (which would wrongly open a news pin under the cursor).
      event.stopPropagation();
      // The chart's own pan/zoom has no per-gesture opt-out, only a
      // whole-chart on/off -- switched off for exactly the duration of the
      // drag and restored in handleMouseUp, so a fast drag doesn't also pan
      // the chart underneath the line.
      chart.applyOptions({ handleScroll: false, handleScale: false });
      draggingRef.current = { ...hit, startPrice: hit.line.options().price };
    };

    // On window, not the container: releasing past the chart's edge (an
    // overshot drag) must still end it, or it would stay "stuck" dragging
    // until the next mousedown inside the chart.
    const handleMouseUp = () => {
      const dragging = draggingRef.current;
      draggingRef.current = null;
      reenableChartInteraction();
      if (!dragging) return;
      try {
        // A click that never actually moved the line commits nothing.
        const price = dragging.line.options().price;
        if (price === dragging.startPrice) return;
        if (dragging.source === "position") {
          onMovePositionLevelRef.current?.(dragging.field, price);
        } else {
          onMoveIndicativeLevelRef.current?.(dragging.field, price);
        }
      } catch {
        // The line was disposed out from under this drag -- see the
        // identical catch in handleMouseMove. Nothing to commit from a line
        // that no longer exists.
      }
    };

    container.addEventListener("mousemove", handleMouseMove);
    container.addEventListener("mousedown", handleMouseDown);
    // Capture phase: a mouseup that lightweight-charts' own internal
    // handling (or anything else between the event target and window)
    // stops propagation on must still reach this, or the drag it ended
    // never gets its pan/zoom re-enabled -- see reenableChartInteraction.
    window.addEventListener("mouseup", handleMouseUp, true);

    return () => {
      // Before remove(), which disposes the time scale -- reaching for
      // timeScale() afterwards throws.
      chart.unsubscribeClick(handleClick);
      chart.timeScale().unsubscribeSizeChange(handleSizeChange);
      container.removeEventListener("mousemove", handleMouseMove);
      container.removeEventListener("mousedown", handleMouseDown);
      window.removeEventListener("mouseup", handleMouseUp, true);
      chart.remove();
      chartRef.current = null;
      priceSeriesRef.current = null;
      volumeSeriesRef.current = null;
      // Belonged to the series just disposed -- left set, the focus effect
      // would call setMarkers on a dead primitive after a remount.
      markersRef.current = null;
      vwapSeriesRef.current = null;
      // Disposed by chart.remove() along with every other series.
      sessionBandsRef.current = null;
      // chart.remove() above already disposed every series/price-line that
      // was attached to it, including whatever the indicators effect added
      // -- without this, those refs would still point at now-disposed
      // objects, and the indicators effect's next run (against a new chart,
      // e.g. after a StrictMode dev remount) would call removeSeries on a
      // series that was never added to it, which lightweight-charts throws
      // on ("Value is undefined" from its internal ensureDefined check).
      priceLinesRef.current = [];
      positionLinesRef.current = [];
      indicativeLinesRef.current = [];
      indicatorSeriesRef.current = [];
      draggableLinesRef.current = [];
      draggingRef.current = null;
    };
  }, []);

  // Creating the price series separately from the chart is what makes the
  // candle/line toggle cheap: only this series is torn down and rebuilt, so
  // the chart, its volume pane and the user's zoom all survive the switch.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const series =
      chartType === "line"
        ? chart.addSeries(LineSeries, {
            // The palette's foreground rather than a colour of its own: a
            // close-only line is the price itself, not one more overlay
            // competing with VWAP and the level lines.
            color:
              getComputedStyle(document.body).getPropertyValue("--text-primary").trim() ||
              "#0b0b0b",
            lineWidth: 2,
            lastValueVisible: true,
          })
        : chart.addSeries(CandlestickSeries, {
            upColor: "#0ca30c",
            downColor: "#d03b3b",
            borderVisible: false,
            wickUpColor: "#0ca30c",
            wickDownColor: "#d03b3b",
          });
    // Leaves the bottom third to the volume histogram, as before.
    series.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.3 } });
    priceSeriesRef.current = series;

    return () => {
      // Guarded on the chart still being live: on unmount the mount effect's
      // chart.remove() has already disposed every series, and removeSeries on
      // a disposed chart throws.
      const liveChart = chartRef.current;
      if (liveChart) {
        // Before the series goes. Without this the toggle snaps back to the
        // default right-anchored window, throwing away whatever the user had
        // scrolled to -- which makes comparing the two renderings of the same
        // stretch of chart impossible.
        pendingRangeRef.current = liveChart.timeScale().getVisibleRange();
      }
      if (liveChart && priceSeriesRef.current) {
        liveChart.removeSeries(priceSeriesRef.current);
      }
      priceSeriesRef.current = null;
      // Both belonged to the series just removed. Left set, the effects that
      // own them would act on a dead object after the swap.
      markersRef.current = null;
      priceLinesRef.current = [];
      positionLinesRef.current = [];
      indicativeLinesRef.current = [];
      draggableLinesRef.current = [];
      draggingRef.current = null;
    };
  }, [chartType]);

  useEffect(() => {
    const priceSeries = priceSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const vwapSeries = vwapSeriesRef.current;
    if (!priceSeries || !volumeSeries || !vwapSeries) return;

    // Narrowed rather than unioned: setData is the one method whose argument
    // genuinely differs between the two series shapes.
    if (chartType === "line") {
      (priceSeries as ISeriesApi<"Line">).setData(bars.map(barToClose));
    } else {
      (priceSeries as ISeriesApi<"Candlestick">).setData(bars.map(barToCandle));
    }
    volumeSeries.setData(bars.map(barToVolume));

    vwapSeries.setData(
      toLinePoints(
        bars,
        (bar) => toUnixSeconds(bar.t),
        (_bar, i) => vwap[i],
      ),
    );

    // An empty list on daily+ charts draws nothing rather than tinting bars
    // that each span whole sessions.
    const bandTimes = shadeSessions ? bars.map((bar) => toUnixSeconds(bar.t)) : [];
    sessionBandsRef.current?.pre.setData(sessionBandData(bandTimes, "pre"));
    sessionBandsRef.current?.post.setData(sessionBandData(bandTimes, "post"));

    const previousBarCount = barCountRef.current;
    barCountRef.current = bars.length;

    // Appended-to or replaced? A live tick appends (or updates the last
    // bar) and leaves the first bar alone; a symbol or timeframe change
    // replaces the whole array, and a logical index then means something
    // new. The distinction decides who owns the viewport below.
    const firstBarTime = bars[0] ? toUnixSeconds(bars[0].t) : null;
    const dataReplaced = firstBarTime !== firstBarTimeRef.current;
    firstBarTimeRef.current = firstBarTime;

    const pendingRange = pendingRangeRef.current;
    pendingRangeRef.current = null;

    // A focused pick owns the viewport: re-applying the default
    // right-anchored range here would immediately scroll away from the bar
    // the user just clicked, on this render and again on every live tick.
    if (focusTime != null) return;

    if (pendingRange) {
      // Same stretch of chart, drawn the other way.
      const chart = chartRef.current;
      if (chart) {
        chart.timeScale().setVisibleRange(pendingRange);
        applyLabelClearance(chart, hasLevelsRef.current);
      }
      return;
    }

    // On a pure append, someone scrolled back keeps their viewport: the
    // existing bars keep their logical indices, so doing nothing is what
    // holds the view still -- while re-applying the default range here
    // yanked them back to the right edge on every live tick, the same
    // stomp the resize handler had. Following the newest bar is only for
    // viewers already at (or near) the right edge.
    if (!dataReplaced && previousBarCount > 0) {
      const current = chartRef.current?.timeScale().getVisibleLogicalRange();
      const atRightEdge = !current || current.to >= previousBarCount - 1.5;
      if (!atRightEdge) return;

      // At the edge with new bars to follow: slide the *current* window
      // forward by exactly how many bars just arrived, rather than falling
      // through to the "fit everything to the pane" recompute below. That
      // recompute picks its own spacing from the total bar count, which
      // drifts as the session goes on and rarely matches whatever zoom the
      // viewer actually has -- so scrolling right, close enough to the edge
      // to count as "following," could still land somewhere else entirely
      // the moment the next tick's effect run reasserted it. A translation
      // keeps the viewer's own zoom and just advances it, which is what
      // "still following" should look like.
      const newBars = bars.length - previousBarCount;
      if (current && newBars > 0) {
        const chart = chartRef.current;
        if (chart) {
          chart.timeScale().setVisibleLogicalRange({ from: current.from + newBars, to: current.to + newBars });
          applyLabelClearance(chart, hasLevelsRef.current);
        }
        return;
      }
    }

    const container = containerRef.current;
    if (container) {
      const range = visibleLogicalRange(container.clientWidth || 1, bars.length);
      const chart = chartRef.current;
      if (range && chart) {
        chart.timeScale().setVisibleLogicalRange(range);
        applyLabelClearance(chart, hasLevelsRef.current);
      }
    }
    // Deliberately NOT dependent on `indicators`: that array gets a fresh
    // identity on every ChartWidget render (it's filtered inline there), so
    // depending on it here would re-run this effect -- and its unconditional
    // setData() calls above -- on renders that carry no new bars at all,
    // fighting the atRightEdge check below and stomping a scrolled-back
    // viewport the same way a live tick could before that check existed.
    // hasLevelsRef (read via applyLabelClearance above) stays current every
    // render regardless. positionLevels is a dependency because it's already
    // stable (memoized upstream on primitives) and the margin has to be
    // re-asserted when it changes for real. chartType is one because the
    // swap above leaves a brand-new, empty series behind -- without it,
    // toggling would blank the price until the next tick.
  }, [bars, vwap, focusTime, positionLevels, chartType, shadeSessions]);

  // Scroll a clicked backtest pick into view and pin it with an arrow. Runs
  // after the data effect above, so the bars it needs are already on the
  // series.
  useEffect(() => {
    const chart = chartRef.current;
    const priceSeries = priceSeriesRef.current;
    if (!chart || !priceSeries) return;

    // Created once and reused, because createSeriesMarkers stacks a fresh
    // primitive layer on the series every call -- so calling it per focus
    // change would pile up layers instead of replacing the marker. The
    // series swap clears this ref, so a toggle re-attaches it to the new
    // series rather than to the disposed one.
    if (!markersRef.current) {
      markersRef.current = createSeriesMarkers(priceSeries, []);
    }
    // One call owns the whole set, so the backtest pick and any "marker"-kind
    // indicator have to be assembled together here rather than each calling
    // setMarkers and wiping the other out.
    //
    // Markers are what a *moment* needs. A price line answers "at what
    // price" and spans the chart; an entry also has to answer "at which bar",
    // and a horizontal line through the entry price cannot say that -- it
    // crosses the chart at every time the price was ever touched.
    const markers: SeriesMarker<Time>[] = [];

    const markerTime = focusTime == null ? null : nearestBarTime(bars, focusTime);
    if (markerTime != null) {
      if (focusTrade) {
        // Entry reuses the same blue positionLevels already uses for a real
        // position's entry line -- same meaning, "this is where it started".
        markers.push({
          time: markerTime,
          position: "belowBar",
          color: POSITION_ENTRY_COLOR,
          shape: "arrowUp",
          text: "Entry",
        });
        const exitTime = nearestBarTime(bars, focusTrade.exitTime);
        if (exitTime != null) {
          markers.push({
            time: exitTime,
            position: "aboveBar",
            color: focusTrade.won ? POSITION_TARGET_COLOR : POSITION_STOP_COLOR,
            shape: "arrowDown",
            text: "Exit",
          });
        }
      } else {
        markers.push({
          time: markerTime,
          position: "aboveBar",
          color: PICK_MARKER_COLOR,
          shape: "arrowDown",
          text: "Pick",
        });
      }
    }

    // indicators is already filtered down to whatever the Levels dropdown
    // has checked, so no separate on/off gate is needed here.
    indicators.forEach((indicator) => {
      if (indicator.kind !== "marker") return;
      Object.entries(indicator.series).forEach(([subName, value]) => {
        if (!isMarkerSeries(value)) return;
        const color = indicator.colors[subName] ?? "#898781";
        value.forEach((marker) => {
          markers.push({
            time: marker.time as Time,
            position: marker.position,
            shape: marker.shape,
            color,
            text: marker.text,
          });
        });
      });
    });

    // News pins: one 📰 per bar, at the bar nearest each headline's publish
    // time. size 0 keeps the emoji as the whole glyph rather than stacking
    // it on a drawn circle. Guarded against stories older than the loaded
    // window -- nearestBarTime would otherwise pin them all to the first
    // bar, which reads as "something happened here" about a moment the
    // chart does not even show. Newer than the last bar (a headline after
    // the close) clamps to the last bar, which is where its session
    // context is.
    const pins = new Map<number, number[]>();
    if (bars.length > 0 && news.length > 0) {
      const firstBarTime = toUnixSeconds(bars[0].t);
      news.forEach((item) => {
        if (item.time < firstBarTime) return;
        const time = nearestBarTime(bars, item.time);
        if (time == null) return;
        const existing = pins.get(time);
        if (existing) {
          // Same bar, second story: no second glyph, but the click still
          // has to surface both.
          existing.push(item.time);
          return;
        }
        pins.set(time as number, [item.time]);
        markers.push({
          time,
          position: "aboveBar",
          shape: "circle",
          size: 0,
          color: "#898781",
          text: "📰",
        });
      });
    }
    newsPinsRef.current = pins;

    // Sorted because the library requires markers in time order and rejects
    // the set otherwise -- the pick and the indicators arrive independently,
    // so nothing else guarantees it.
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    markersRef.current.setMarkers(markers);

    if (focusTime == null || bars.length === 0) {
      focusRangeRef.current = null;
      return;
    }

    // Index-based, not time-based -- see nearestBarIndex's own comment for
    // why setVisibleRange (time-based) doesn't reliably stick on this chart.
    const lastIndex = bars.length - 1;
    const clamp = (index: number) => Math.min(Math.max(index, 0), lastIndex);
    const entryIndex = clamp(nearestBarIndex(bars, focusTime) ?? 0);
    // Without a trade, this collapses to entryIndex on both ends -- same
    // single-point-plus-padding window a backtest pick always got.
    const exitIndex = focusTrade ? clamp(nearestBarIndex(bars, focusTrade.exitTime) ?? entryIndex) : entryIndex;
    const rangeStart = Math.min(entryIndex, exitIndex);
    const rangeEnd = Math.max(entryIndex, exitIndex);

    const req = {
      from: Math.max(0, rangeStart - FOCUS_PADDING_BARS),
      to: Math.min(lastIndex, rangeEnd + FOCUS_PADDING_BARS),
    };
    focusRangeRef.current = req;
    const ts = chart.timeScale();

    // A single call here isn't reliable on a symbol with bars still
    // actively arriving (confirmed live: on an actively-updating intraday
    // chart, something -- not shiftVisibleRangeOnNewBar, already off above,
    // and not reproducible on a symbol whose bars have stopped changing --
    // repeatedly snaps the range back to the default right-anchored view
    // within a couple hundred ms of this call, self-resolving only once
    // updates settle down; a live symbol's updates don't reliably do that
    // inside a human-perceptible window). Reasserting a few times over the
    // next second outlasts that without a visible fight: each call is
    // idempotent once it's already the current range, so once it sticks the
    // remaining calls are no-ops.
    if (reassertTimeoutRef.current != null) {
      window.clearTimeout(reassertTimeoutRef.current);
      reassertTimeoutRef.current = null;
    }
    applyingRangeRef.current = true;
    let attempt = 0;
    const reassert = () => {
      ts.setVisibleLogicalRange(req);
      attempt += 1;
      if (attempt < 6) {
        reassertTimeoutRef.current = window.setTimeout(reassert, attempt * 80);
      } else {
        reassertTimeoutRef.current = null;
        applyingRangeRef.current = false;
      }
    };
    reassert();
    return () => {
      if (reassertTimeoutRef.current != null) {
        window.clearTimeout(reassertTimeoutRef.current);
        reassertTimeoutRef.current = null;
      }
    };
  }, [focusTime, focusTrade, bars, chartType, indicators, news]);

  // Separate from the bars/vwap effect above: indicators only change when
  // the symbol changes or the toggle flips, not on every live tick, and
  // price lines/series have to be explicitly removed -- unlike
  // series.setData, there's no bulk "replace" for createPriceLine, and a
  // "series"-kind indicator (e.g. an EMA) is its own chart series, not
  // just a value on the existing candle series.
  useEffect(() => {
    const chart = chartRef.current;
    const priceSeries = priceSeriesRef.current;
    if (!chart || !priceSeries) return;

    // Logical (bar-index) range, not time-based: this effect never touches
    // `bars` itself, only price lines and indicator series, so the bar count
    // is provably unchanged between the capture below and the restore at the
    // end -- capturing by index is exact, with no time-to-index resolution
    // for the library to redo.
    //
    // A time-based range was used here previously, on the theory that a
    // "series"-kind indicator (e.g. an EMA, sourced from 1-minute bars
    // regardless of the displayed timeframe) could be a different
    // resolution than the candles, making a logical index mean something
    // different by the time it was restored. That doesn't hold up against
    // aggregateBars, which already rebuckets every "series" sub-series to
    // match `bars` one-for-one before either reaches this component -- so
    // logical and time-based describe the same window here, and logical is
    // the one with no round-trip to get wrong. On a multi-day GDXD session
    // with overnight/weekend gaps, the time-based restore was occasionally
    // resolving to the wrong bar and snapping the view to the right edge --
    // this effect reruns on every live tick even when `indicators` is
    // unchanged content-wise (ChartWidget filters it inline into a fresh
    // array each render), so that resolution ran far more often than a
    // toggle in the Levels dropdown alone would suggest.
    const preservedRange = chart.timeScale().getVisibleLogicalRange();

    priceLinesRef.current.forEach((line) => priceSeries.removePriceLine(line));
    priceLinesRef.current = [];
    indicatorSeriesRef.current.forEach((series) => chart.removeSeries(series));
    indicatorSeriesRef.current = [];

    // indicators is already filtered down to whatever the Levels dropdown
    // has checked, so no separate on/off gate is needed here.
    indicators.forEach((indicator) => {
      Object.entries(indicator.series).forEach(([subName, value]) => {
        const color = indicator.colors[subName] ?? "#898781";
        // "EMA" + "EMA 9" reads as "EMA EMA 9" and pushes the label that
        // much further over the candles. Where the sub-series already names
        // its group, the group name adds nothing.
        const title = subName.startsWith(indicator.name) ? subName : `${indicator.name} ${subName}`;

        if (indicator.kind === "level") {
          if (typeof value !== "number") return;
          const style = resolveStyle(indicator.style, DEFAULT_LEVEL_STYLE);
          const line = priceSeries.createPriceLine({
            price: value,
            color,
            lineWidth: style.width as 1 | 2 | 3 | 4,
            lineStyle: style.dash,
            axisLabelVisible: true,
            title,
          });
          priceLinesRef.current.push(line);
        } else if (indicator.kind === "series" && isPointSeries(value)) {
          const style = resolveStyle(indicator.style, DEFAULT_SERIES_STYLE);
          const series = chart.addSeries(LineSeries, {
            color,
            lineWidth: style.width as 1 | 2 | 3 | 4,
            lineStyle: style.dash,
            crosshairMarkerVisible: false,
            lastValueVisible: true,
            title,
          });
          series.setData(
            toLinePoints(
              value,
              (p) => toUnixSeconds(p.t),
              (p) => p.value,
            ),
          );
          indicatorSeriesRef.current.push(series);
        }
      });
    });

    // Skipped while a focus (backtest pick or journal trade) is driving the
    // viewport: capturing right after the focus effect's own setVisibleRange
    // (same commit, effects run in declaration order, this one after that
    // one) can read back a logical range the library hasn't finished
    // recomputing yet for the new time-based range -- restoring that stale
    // read would silently undo the scroll-to-focus the user just triggered.
    // The unfocused case (this guard's normal path) is unaffected: nothing
    // upstream just moved the viewport, so the preserved/restored range is
    // always a faithful echo of wherever the user actually left it.
    if (preservedRange && focusTime == null) {
      chart.timeScale().setVisibleLogicalRange(preservedRange);
    }
    // After the restore, never before: setVisibleLogicalRange repositions
    // the content and drops the margin.
    applyLabelClearance(chart, hasAnyVisibleLevel(indicators, positionLevels, indicativeLevels));
    // chartType, because the price lines live on the price series and the
    // swap disposes them along with it. positionLevels/indicativeLevels,
    // because they also feed the margin call above.
  }, [indicators, positionLevels, indicativeLevels, chartType]);

  // Its own effect rather than folded into the indicators effect above: that
  // one's clear-and-rebuild lifecycle is specifically about the `indicators`
  // prop, and sharing it would mean a position line flickers/rebuilds on
  // every indicator toggle for no reason, and vice versa.
  useEffect(() => {
    const priceSeries = priceSeriesRef.current;
    if (!priceSeries) return;

    // Before disposing anything below -- see cancelActiveDrag's own comment.
    cancelActiveDrag("position");

    positionLinesRef.current.forEach((line) => priceSeries.removePriceLine(line));
    positionLinesRef.current = [];
    // Only this source's entries -- the indicative-lines effect owns the
    // rest of the list and runs independently.
    draggableLinesRef.current = draggableLinesRef.current.filter((l) => l.source !== "position");

    if (positionLevels) {
      const { entry, stop, target } = positionLevels;
      if (entry != null) {
        positionLinesRef.current.push(
          priceSeries.createPriceLine({
            price: entry,
            color: POSITION_ENTRY_COLOR,
            lineWidth: POSITION_LINE_WIDTH,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: "Entry",
          }),
        );
      }
      // Draggable only when the caller can actually do something about the
      // drop -- onMovePositionLevelRef.current undefined means nothing
      // wired it up; stop/targetOrderId null (checked upstream, before this
      // prop is even built) means there's no order there to move. Either
      // way, a line that can't be moved shouldn't invite the attempt. Read
      // via the ref, not the prop itself, so this effect isn't also forced
      // to rerun (tearing down and rebuilding every line) on every poll
      // tick just because the callback ChartWidget passes is rebuilt from
      // moveStop/moveTarget, which are fresh functions every render of
      // useTrading() -- the same non-memoizable-action-callback problem
      // `indicators` had before it was pulled out of this effect's deps.
      if (stop != null) {
        const line = priceSeries.createPriceLine({
          price: stop,
          color: POSITION_STOP_COLOR,
          lineWidth: POSITION_LINE_WIDTH,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: "Stop",
        });
        positionLinesRef.current.push(line);
        if (onMovePositionLevelRef.current) {
          draggableLinesRef.current.push({ line, source: "position", field: "stop" });
        }
      }
      if (target != null) {
        const line = priceSeries.createPriceLine({
          price: target,
          color: POSITION_TARGET_COLOR,
          lineWidth: POSITION_LINE_WIDTH,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: "Target",
        });
        positionLinesRef.current.push(line);
        if (onMovePositionLevelRef.current) {
          draggableLinesRef.current.push({ line, source: "position", field: "target" });
        }
      }
    }
    // chartType, because the position lines live on the price series and the
    // candle/line swap disposes them along with it.
  }, [positionLevels, chartType]);

  // Same shape as the positionLevels effect just above, own effect for the
  // same reason -- but dashed (INDICATIVE_LINE_STYLE) and titled "(draft)"
  // so these can never be mistaken for a real position's lines even where
  // both are showing at once (see indicativeLevels' own doc comment).
  useEffect(() => {
    const priceSeries = priceSeriesRef.current;
    if (!priceSeries) return;

    // Before disposing anything below -- see cancelActiveDrag's own comment.
    cancelActiveDrag("indicative");

    indicativeLinesRef.current.forEach((line) => priceSeries.removePriceLine(line));
    indicativeLinesRef.current = [];
    // Only this source's entries -- see the equivalent line in the
    // positionLevels effect above.
    draggableLinesRef.current = draggableLinesRef.current.filter((l) => l.source !== "indicative");

    if (indicativeLevels) {
      const { entry, stop, target } = indicativeLevels;
      if (entry != null) {
        indicativeLinesRef.current.push(
          priceSeries.createPriceLine({
            price: entry,
            color: POSITION_ENTRY_COLOR,
            lineWidth: INDICATIVE_LINE_WIDTH,
            lineStyle: INDICATIVE_LINE_STYLE,
            axisLabelVisible: true,
            title: "Entry (draft)",
          }),
        );
      }
      // Read via the ref, not the prop -- see the equivalent comment in the
      // positionLevels effect above for why.
      if (stop != null) {
        const line = priceSeries.createPriceLine({
          price: stop,
          color: POSITION_STOP_COLOR,
          lineWidth: INDICATIVE_LINE_WIDTH,
          lineStyle: INDICATIVE_LINE_STYLE,
          axisLabelVisible: true,
          title: "Stop (draft)",
        });
        indicativeLinesRef.current.push(line);
        if (onMoveIndicativeLevelRef.current) {
          draggableLinesRef.current.push({ line, source: "indicative", field: "stop" });
        }
      }
      if (target != null) {
        const line = priceSeries.createPriceLine({
          price: target,
          color: POSITION_TARGET_COLOR,
          lineWidth: INDICATIVE_LINE_WIDTH,
          lineStyle: INDICATIVE_LINE_STYLE,
          axisLabelVisible: true,
          title: "Target (draft)",
        });
        indicativeLinesRef.current.push(line);
        if (onMoveIndicativeLevelRef.current) {
          draggableLinesRef.current.push({ line, source: "indicative", field: "target" });
        }
      }
    }
    // chartType, same reason as the positionLevels effect.
  }, [indicativeLevels, chartType]);

  // Its own effect, so switching the cursor does not tear the chart down and
  // lose the zoom -- the same reason the series swap has one.
  useEffect(() => {
    chartRef.current?.applyOptions({ crosshair: { mode: CROSSHAIR_MODES[cursorMode] } });
  }, [cursorMode]);

  // The class drives the CSS cursor. Hiding the crosshair does not change the
  // pointer the library draws, so the two have to be set together or
  // "pointer" would hide the lines and still show a crosshair cursor.
  return <div ref={containerRef} className={`chart-container cursor-${cursorMode}`} />;
}
