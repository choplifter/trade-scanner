import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
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

/** Candles carry open/high/low; a line carries only the close. Both read the
 * same bars -- the line is for seeing the shape of a move without the wicks,
 * which on a thin premarket tape is mostly noise. */
export type ChartType = "candles" | "line";

interface CandleChartProps {
  bars: Bar[];
  chartType: ChartType;
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
function applyLabelClearance(chart: IChartApi, showIndicators: boolean) {
  chart.timeScale().applyOptions({
    rightOffsetPixels: showIndicators ? LEVEL_LABEL_CLEARANCE_PX : 0,
  });
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
  showIndicators,
  focusTime,
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
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const indicatorSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  // The markers primitive, kept so the pick pin is updated in place rather
  // than layered again on every focus change.
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  // Read by the resize handler, which is subscribed once at mount and so
  // can't close over the current bars. A ref rather than a dep of the mount
  // effect, which would rebuild the whole chart on every tick.
  const barCountRef = useRef(0);
  // The resize handler is built once in the mount effect and cannot close
  // over the prop -- it would keep first render's value forever.
  const showIndicatorsRef = useRef(showIndicators);
  showIndicatorsRef.current = showIndicators;
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
      localization: { timeFormatter: crosshairTimeFormatter },
      autoSize: true,
    });

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
      applyLabelClearance(chart, showIndicatorsRef.current);
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
      priceSeriesRef.current = null;
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

    barCountRef.current = bars.length;

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
        applyLabelClearance(chart, showIndicators);
      }
      return;
    }

    const container = containerRef.current;
    if (container) {
      const range = visibleLogicalRange(container.clientWidth || 1, bars.length);
      const chart = chartRef.current;
      if (range && chart) {
        chart.timeScale().setVisibleLogicalRange(range);
        applyLabelClearance(chart, showIndicators);
      }
    }
    // showIndicators is a dependency because this effect repositions the
    // viewport, and the margin has to be re-asserted whenever it does.
    // chartType is one because the swap above leaves a brand-new, empty
    // series behind -- without it, toggling would blank the price until the
    // next tick.
  }, [bars, vwap, focusTime, showIndicators, chartType]);

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
      markers.push({
        time: markerTime,
        position: "aboveBar",
        color: PICK_MARKER_COLOR,
        shape: "arrowDown",
        text: "Pick",
      });
    }

    if (showIndicators) {
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
    }

    // Sorted because the library requires markers in time order and rejects
    // the set otherwise -- the pick and the indicators arrive independently,
    // so nothing else guarantees it.
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    markersRef.current.setMarkers(markers);

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
  }, [focusTime, bars, chartType, indicators, showIndicators]);

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

    // Adding/removing a "series"-kind indicator (e.g. an EMA, sourced from
    // 1-minute bars regardless of the displayed timeframe) can be a much
    // higher time-resolution than the currently-displayed candles -- that
    // changes what a *logical* bar index even means, so preserving by
    // logical index (as opposed to by actual time) still lets the visible
    // window jump. Time-based range is immune to that: it's the same wall-
    // clock window regardless of how many logical points now exist inside
    // it.
    const preservedRange = chart.timeScale().getVisibleRange();

    priceLinesRef.current.forEach((line) => priceSeries.removePriceLine(line));
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
    }

    if (preservedRange) {
      chart.timeScale().setVisibleRange(preservedRange);
    }
    // After the restore, never before: setVisibleRange repositions the
    // content and drops the margin.
    applyLabelClearance(chart, showIndicators);
    // chartType, because the price lines live on the price series and the
    // swap disposes them along with it.
  }, [indicators, showIndicators, chartType]);

  return <div ref={containerRef} className="chart-container" />;
}
