import { useEffect, useMemo, useState } from "react";

import { useChartFeed } from "../../hooks/useChartFeed";
import type { ChartFocus } from "../../types/screener";
import { useHistoricalBars } from "../../hooks/useHistoricalBars";
import { aggregateBars, TIMEFRAME_OPTIONS } from "../../utils/aggregateBars";
import { formatPrice } from "../../utils/format";
import { CandleChart } from "./CandleChart";
import { SymbolInfoPanel } from "./SymbolInfoPanel";

interface ChartWidgetProps {
  symbol: string | null;
  /** Set when a backtest pick is clicked: jump to this entry and show it at
   * a resolution where it's visible. */
  focus?: ChartFocus | null;
}

const DEFAULT_TIMEFRAME_KEY = "5m";

export function ChartWidget({ symbol, focus }: ChartWidgetProps) {
  const [timeframeKey, setTimeframeKey] = useState(DEFAULT_TIMEFRAME_KEY);

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
  const [showIndicators, setShowIndicators] = useState(false);
  const option =
    TIMEFRAME_OPTIONS.find((o) => o.key === timeframeKey) ??
    TIMEFRAME_OPTIONS.find((o) => o.key === DEFAULT_TIMEFRAME_KEY)!;

  // Always kept live regardless of the selected timeframe: it's the source
  // for the intraday buckets below, and also drives the header's last-price
  // (which should track real trades even while looking at a Daily chart).
  const intraday = useChartFeed(symbol);
  const historical = useHistoricalBars(
    symbol,
    option.kind === "historical" ? (option.alpacaTimeframe ?? null) : null,
  );

  const displayed = useMemo(() => {
    if (option.kind === "intraday") {
      return aggregateBars(intraday.bars, intraday.vwap, option.minutes ?? 1, intraday.indicators);
    }
    // "series"-kind indicators (e.g. an EMA) are minute-resolution -- on an
    // hourly/daily/weekly/monthly chart that's both semantically odd to
    // overlay and, left unaggregated, would trip the same
    // resolution-mismatch zoom bug aggregateBars exists to avoid. "level"
    // lines are flat values, unaffected either way, so only those show here.
    return {
      bars: historical.bars,
      vwap: historical.vwap,
      indicators: intraday.indicators.filter((i) => i.kind === "level"),
    };
  }, [option, intraday.bars, intraday.vwap, intraday.indicators, historical.bars, historical.vwap]);

  const lastPrice =
    intraday.bars[intraday.bars.length - 1]?.c ??
    displayed.bars[displayed.bars.length - 1]?.c ??
    null;

  const activeFeed = option.kind === "intraday" ? intraday : historical;
  const noBarsYet =
    option.kind === "intraday" && !intraday.loading && intraday.bars.length === 0 && !intraday.error;
  const noHistoricalData =
    option.kind === "historical" &&
    !historical.loading &&
    historical.bars.length === 0 &&
    !historical.error;

  return (
    <div className="widget chart-widget">
      <div className="widget-header">
        <div className="chart-toolbar">
          <span className="symbol">{symbol ?? "Select a symbol"}</span>
          {lastPrice != null && <span className="last-price">{formatPrice(lastPrice)}</span>}
        </div>
        <div className="chart-toolbar">
          <div className="timeframe-selector" role="group" aria-label="Chart timeframe">
            {TIMEFRAME_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                className="timeframe-button"
                aria-pressed={timeframeKey === opt.key}
                onClick={() => setTimeframeKey(opt.key)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {option.kind === "intraday" && (
            <span className="vwap-legend">
              <span className="vwap-swatch" /> VWAP
            </span>
          )}
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={showIndicators}
            onClick={() => setShowIndicators((v) => !v)}
            title="Toggle premarket/weekly/monthly range lines"
          >
            Levels
          </button>
        </div>
      </div>
      <div className="widget-body">
        {!symbol ? (
          <div className="widget-empty">Click a symbol in a scanner to load its chart.</div>
        ) : activeFeed.error ? (
          <div className="widget-error">{activeFeed.error}</div>
        ) : activeFeed.loading && displayed.bars.length === 0 ? (
          <div className="widget-empty">Loading {symbol}…</div>
        ) : noBarsYet ? (
          <div className="widget-empty">
            No trades printed on the IEX feed for {symbol} yet today. Premarket volume on IEX is
            thin — this fills in once trades start (most reliably at 9:30 ET open).
          </div>
        ) : noHistoricalData ? (
          <div className="widget-empty">No {option.label} history available for {symbol}.</div>
        ) : (
          <CandleChart
            bars={displayed.bars}
            vwap={displayed.vwap}
            indicators={displayed.indicators}
            showIndicators={showIndicators}
            // Only honour the focus while it still refers to the symbol on
            // screen; a stale one would drag the chart to an unrelated time
            // after the user clicks a different row.
            focusTime={focus && focus.symbol === symbol ? focus.time : null}
          />
        )}
      </div>
      {symbol && <SymbolInfoPanel symbol={symbol} />}
    </div>
  );
}
