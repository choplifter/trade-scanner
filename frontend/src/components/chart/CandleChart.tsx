import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  TickMarkType,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type Time,
  type WhitespaceData,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Bar, IndicatorResult } from "../../types/alpaca";

interface CandleChartProps {
  bars: Bar[];
  vwap: (number | null)[];
  indicators: IndicatorResult[];
  showIndicators: boolean;
  /** Unix seconds to scroll into view — a backtest pick's entry time. Null
   * leaves the chart wherever the user left it. */
  focusTime?: number | null;
}

/** How much history/future to show either side of a focused pick. Wide
 * enough to see what led into the entry and what happened after, narrow
 * enough that the bar in question is still identifiable. */
const FOCUS_PADDING_SECONDS = 90 * 60;

/** Same pin the Dash backtest page drops on a clicked pick (see
 * dash_app/assets/lightweight_chart.html's markPick), so the two surfaces
 * mark an entry identically. */
const PICK_MARKER_COLOR = "#2a78d6";

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

function toUnixSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

// lightweight-charts formats axis/crosshair labels using the Date object's
// UTC getters, so by default every label shows UTC time regardless of the
// viewer's timezone. Intl.DateTimeFormat with no explicit `timeZone` uses
// the browser's local timezone, so overriding both formatters with it makes
// the chart display in whatever timezone the viewer is actually in.
const TIME_FORMAT = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
const DAY_FORMAT = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short" });
const MONTH_FORMAT = new Intl.DateTimeFormat(undefined, { month: "short", year: "numeric" });
const YEAR_FORMAT = new Intl.DateTimeFormat(undefined, { year: "numeric" });
const CROSSHAIR_FORMAT = new Intl.DateTimeFormat(undefined, {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function tickMarkFormatter(time: Time, tickMarkType: TickMarkType): string {
  const date = new Date((time as number) * 1000);
  switch (tickMarkType) {
    case TickMarkType.Year:
      return YEAR_FORMAT.format(date);
    case TickMarkType.Month:
      return MONTH_FORMAT.format(date);
    case TickMarkType.DayOfMonth:
      return DAY_FORMAT.format(date);
    default:
      return TIME_FORMAT.format(date);
  }
}

function barToCandle(bar: Bar): CandlestickData {
  return { time: toUnixSeconds(bar.t), open: bar.o, high: bar.h, low: bar.l, close: bar.c };
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

// Width the right price scale is held at while level lines are shown.
//
// Their axis labels are badges, not ticks -- "Monthly Range High 12.91",
// "VWAP +1 SD 14.23" -- and when they are wider than the scale they spill
// left over the plot, landing on the newest candles, which are the ones
// pinned to the right edge.
//
// This widens the scale so the labels have somewhere to sit. The first
// attempt instead padded the visible logical range to push the candles left,
// which did nothing: setVisibleLogicalRange clamps `to` at the last bar, so
// the range came back unchanged and the measured shift was zero. minimumWidth
// is independent of the range logic and so cannot be clamped by it.
const LEVEL_LABEL_SCALE_WIDTH = 190;

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


export function CandleChart({ bars, vwap, indicators, showIndicators, focusTime }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const indicatorSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  // The markers primitive, kept so the pick pin is updated in place rather
  // than layered again on every focus change.
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  // Read by the resize handler, which is subscribed once at mount and so
  // can't close over the current bars. A ref rather than a dep of the mount
  // effect, which would rebuild the whole chart on every tick.
  const barCountRef = useRef(0);
  // Applying a range can itself change the time scale's width (different
  // visible bars -> different price labels -> a wider/narrower price scale),
  // which fires subscribeSizeChange again. This swallows that echo.
  const applyingRangeRef = useRef(false);

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
      },
      localization: {
        timeFormatter: (time: Time) => CROSSHAIR_FORMAT.format(new Date((time as number) * 1000)),
      },
      autoSize: true,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#0ca30c",
      downColor: "#d03b3b",
      borderVisible: false,
      wickUpColor: "#0ca30c",
      wickDownColor: "#d03b3b",
    });
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.3 } });

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
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    vwapSeriesRef.current = vwapSeries;

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
      const range = visibleLogicalRange(container.clientWidth || 1, barCountRef.current);
      if (!range) return;
      applyingRangeRef.current = true;
      chart.timeScale().setVisibleLogicalRange(range);
      requestAnimationFrame(() => {
        applyingRangeRef.current = false;
      });
    };
    chart.timeScale().subscribeSizeChange(handleSizeChange);

    return () => {
      // Before remove(), which disposes the time scale -- reaching for
      // timeScale() afterwards throws.
      chart.timeScale().unsubscribeSizeChange(handleSizeChange);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      // Belonged to the series just disposed -- left set, the focus effect
      // would call setMarkers on a dead primitive after a remount.
      markersRef.current = null;
      vwapSeriesRef.current = null;
      // chart.remove() above already disposed every series/price-line that
      // was attached to it, including whatever the indicators effect added
      // -- without this, those refs would still point at now-disposed
      // objects, and the indicators effect's next run (against a new chart,
      // e.g. after a StrictMode dev remount) would call removeSeries on a
      // series that was never added to it, which lightweight-charts throws
      // on ("Value is undefined" from its internal ensureDefined check).
      priceLinesRef.current = [];
      indicatorSeriesRef.current = [];
    };
  }, []);

  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const vwapSeries = vwapSeriesRef.current;
    if (!candleSeries || !volumeSeries || !vwapSeries) return;

    candleSeries.setData(bars.map(barToCandle));
    volumeSeries.setData(bars.map(barToVolume));

    vwapSeries.setData(
      toLinePoints(
        bars,
        (bar) => toUnixSeconds(bar.t),
        (_bar, i) => vwap[i],
      ),
    );

    barCountRef.current = bars.length;

    // A focused pick owns the viewport: re-applying the default
    // right-anchored range here would immediately scroll away from the bar
    // the user just clicked, on this render and again on every live tick.
    if (focusTime != null) return;

    const container = containerRef.current;
    if (container) {
      const range = visibleLogicalRange(container.clientWidth || 1, bars.length);
      if (range) {
        chartRef.current?.timeScale().setVisibleLogicalRange(range);
      }
    }
  }, [bars, vwap, focusTime]);

  // Scroll a clicked backtest pick into view and pin it with an arrow. Runs
  // after the data effect above, so the bars it needs are already on the
  // series.
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;

    // Created once and reused, because createSeriesMarkers stacks a fresh
    // primitive layer on the series every call -- so calling it per focus
    // change would pile up layers instead of replacing the marker.
    if (!markersRef.current) {
      markersRef.current = createSeriesMarkers(candleSeries, []);
    }
    const markerTime = focusTime == null ? null : nearestBarTime(bars, focusTime);
    markersRef.current.setMarkers(
      markerTime == null
        ? []
        : [
            {
              time: markerTime,
              position: "aboveBar",
              color: PICK_MARKER_COLOR,
              shape: "arrowDown",
              text: "Pick",
            },
          ],
    );

    if (focusTime == null || bars.length === 0) return;

    const first = toUnixSeconds(bars[0].t);
    const last = toUnixSeconds(bars[bars.length - 1].t);
    // Silently doing nothing would look like a broken click. Clamping keeps
    // the chart pointed at the nearest end of what it actually has, which at
    // least shows the user the pick is outside the loaded window.
    const target = Math.min(Math.max(focusTime, first), last);

    chart.timeScale().setVisibleRange({
      from: Math.max(first, target - FOCUS_PADDING_SECONDS) as UTCTimestamp,
      to: Math.min(last, target + FOCUS_PADDING_SECONDS) as UTCTimestamp,
    });
  }, [focusTime, bars]);

  // Separate from the bars/vwap effect above: indicators only change when
  // the symbol changes or the toggle flips, not on every live tick, and
  // price lines/series have to be explicitly removed -- unlike
  // series.setData, there's no bulk "replace" for createPriceLine, and a
  // "series"-kind indicator (e.g. an EMA) is its own chart series, not
  // just a value on the existing candle series.
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;

    // Adding/removing a "series"-kind indicator (e.g. an EMA, sourced from
    // 1-minute bars regardless of the displayed timeframe) can be a much
    // higher time-resolution than the currently-displayed candles -- that
    // changes what a *logical* bar index even means, so preserving by
    // logical index (as opposed to by actual time) still lets the visible
    // window jump. Time-based range is immune to that: it's the same wall-
    // clock window regardless of how many logical points now exist inside
    // it.
    const preservedRange = chart.timeScale().getVisibleRange();

    // Give the labels their own room, or take it back. Done here rather than
    // in its own effect so the width and the lines that need it always change
    // together.
    chart.priceScale("right").applyOptions({
      minimumWidth: showIndicators ? LEVEL_LABEL_SCALE_WIDTH : 0,
    });

    priceLinesRef.current.forEach((line) => candleSeries.removePriceLine(line));
    priceLinesRef.current = [];
    indicatorSeriesRef.current.forEach((series) => chart.removeSeries(series));
    indicatorSeriesRef.current = [];

    if (showIndicators) {
      indicators.forEach((indicator) => {
        Object.entries(indicator.series).forEach(([subName, value]) => {
          const color = indicator.colors[subName] ?? "#898781";
          // "EMA" + "EMA 9" reads as "EMA EMA 9" and pushes the label that
          // much further over the candles. Where the sub-series already names
          // its group, the group name adds nothing.
          const title = subName.startsWith(indicator.name) ? subName : `${indicator.name} ${subName}`;

          if (indicator.kind === "level") {
            if (typeof value !== "number") return;
            const line = candleSeries.createPriceLine({
              price: value,
              color,
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title,
            });
            priceLinesRef.current.push(line);
          } else if (indicator.kind === "series" && Array.isArray(value)) {
            const series = chart.addSeries(LineSeries, {
              color,
              lineWidth: 2,
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
    }

    if (preservedRange) {
      chart.timeScale().setVisibleRange(preservedRange);
    }
  }, [indicators, showIndicators]);

  return <div ref={containerRef} className="chart-container" />;
}
