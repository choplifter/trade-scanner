import { useEffect, useRef } from "react";
import {
  BaselineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { ChartPalette } from "../../api/chartTheme";
import { numberLocale } from "../../api/settings";
import { useChartPalette } from "../../hooks/useSettings";
import type { BalancePoint } from "../../types/trading";
import {
  crosshairDateFormatter,
  crosshairTimeFormatter,
  tickMarkFormatter,
} from "../../utils/chartTime";

interface BalanceChartProps {
  points: BalancePoint[];
  /** Sampled once per session, so the crosshair should read as a date rather
   * than a meaningless 00:00. */
  daily: boolean;
}


/**
 * The account equity curve.
 *
 * A baseline series rather than a plain area, anchored at the first plotted
 * balance: the question this chart answers is "am I up or down over this
 * window", and a baseline answers it by colour without the reader having to
 * compare the line against an axis label. The anchor moves with the range,
 * because "up over the last week" and "up since the account opened" are
 * different questions and each range asks its own.
 *
 * Built once and mutated imperatively, the same way CandleChart is: the
 * chart owns its canvas, and re-creating it whenever the poll returns would
 * throw away the user's zoom every 30 seconds.
 */
function baselineColors(palette: ChartPalette) {
  return {
    topLineColor: palette.up,
    topFillColor1: palette.upSoft.replace(/[\d.]+\)$/, "0.28)"),
    topFillColor2: palette.upSoft.replace(/[\d.]+\)$/, "0.02)"),
    bottomLineColor: palette.down,
    bottomFillColor1: palette.downSoft.replace(/[\d.]+\)$/, "0.02)"),
    bottomFillColor2: palette.downSoft.replace(/[\d.]+\)$/, "0.28)"),
  };
}

export function BalanceChart({ points, daily }: BalanceChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Baseline"> | null>(null);
  // The crosshair formatter is installed once at mount and cannot close over
  // a prop that changes when the range does.
  const dailyRef = useRef(daily);
  dailyRef.current = daily;
  // The Settings dialog's colour scheme; applied to the live series below.
  const { palette } = useChartPalette();
  const paletteRef = useRef(palette);
  paletteRef.current = palette;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const styles = getComputedStyle(document.body);
    const textColor = styles.getPropertyValue("--text-secondary").trim() || "#888888";
    const gridColor = styles.getPropertyValue("--gridline").trim() || "#2c2c2a";

    const chart = createChart(container, {
      layout: { background: { color: "transparent" }, textColor },
      grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
      timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter },
      localization: {
        timeFormatter: (time: Time) =>
          dailyRef.current ? crosshairDateFormatter(time) : crosshairTimeFormatter(time),
        // Equity is money, and the default 2-significant-digit formatting
        // renders a six-figure balance as "97.57k" -- which is the one thing
        // on this panel a reader wants exactly.
        priceFormatter: (price: number) =>
          price.toLocaleString(numberLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      },
      autoSize: true,
      handleScale: { axisPressedMouseMove: false },
    });

    const series = chart.addSeries(BaselineSeries, {
      lineWidth: 2,
      ...baselineColors(paletteRef.current),
      priceLineVisible: false,
    });
    series.priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: 0.12 } });

    chartRef.current = chart;
    seriesRef.current = series;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    seriesRef.current?.applyOptions(baselineColors(palette));
  }, [palette]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;

    // Alpaca can report two samples on the same timestamp around a session
    // boundary, and lightweight-charts throws on non-ascending time ("data
    // must be asc ordered by time") rather than ignoring the duplicate, so
    // the last value for any repeated timestamp wins.
    const byTime = new Map<number, number>();
    for (const point of points) byTime.set(point.t, point.equity);
    const data = [...byTime.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([t, equity]) => ({ time: t as UTCTimestamp, value: equity }));

    // The anchor is the first plotted balance, so the shaded region reads as
    // profit and loss over exactly the window on screen.
    series.applyOptions({
      baseValue: { type: "price", price: data.length > 0 ? data[0].value : 0 },
    });
    series.setData(data);
    // An equity curve is read whole -- there is no "most recent N bars" the
    // way there is on a price chart, so the range always fits its content.
    chart.timeScale().fitContent();
  }, [points]);

  return <div ref={containerRef} className="chart-container" />;
}
