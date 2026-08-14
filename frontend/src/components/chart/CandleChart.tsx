import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  LineStyle,
  TickMarkType,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineData,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Bar, IndicatorResult } from "../../types/alpaca";

interface CandleChartProps {
  bars: Bar[];
  vwap: (number | null)[];
  indicators: IndicatorResult[];
  showIndicators: boolean;
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

// Chart instance is created once and mutated imperatively via the
// lightweight-charts API rather than re-rendered through React, since it
// owns its own canvas and re-creating it per tick would be far too slow for
// live data.
export function CandleChart({ bars, vwap, indicators, showIndicators }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const indicatorSeriesRef = useRef<ISeriesApi<"Line">[]>([]);

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

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
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

    const vwapPoints: LineData[] = [];
    bars.forEach((bar, i) => {
      const value = vwap[i];
      if (value != null) {
        vwapPoints.push({ time: toUnixSeconds(bar.t), value });
      }
    });
    vwapSeries.setData(vwapPoints);

    // Show as many bars as fit at a readable spacing: fit everything when
    // there's little data (capped at MAX_SPACING so a handful of bars
    // doesn't stretch into oversized candles), or the most recent bars at
    // MIN_SPACING when there's more data than the pane can show at once.
    const container = containerRef.current;
    if (container && bars.length > 0) {
      const MIN_SPACING = 3;
      const MAX_SPACING = 10;
      const width = container.clientWidth || 1;
      const spacing = Math.min(MAX_SPACING, Math.max(MIN_SPACING, width / bars.length));
      const visibleCount = Math.min(bars.length, Math.max(1, Math.ceil(width / spacing)));
      chartRef.current?.timeScale().setVisibleLogicalRange({
        from: bars.length - visibleCount,
        to: bars.length - 1,
      });
    }
  }, [bars, vwap]);

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

    priceLinesRef.current.forEach((line) => candleSeries.removePriceLine(line));
    priceLinesRef.current = [];
    indicatorSeriesRef.current.forEach((series) => chart.removeSeries(series));
    indicatorSeriesRef.current = [];

    if (showIndicators) {
      indicators.forEach((indicator) => {
        Object.entries(indicator.series).forEach(([subName, value]) => {
          const color = indicator.colors[subName] ?? "#898781";
          const title = `${indicator.name} ${subName}`;

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
            const points: LineData[] = value
              .filter((p) => p.value != null)
              .map((p) => ({ time: toUnixSeconds(p.t), value: p.value as number }));
            series.setData(points);
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
