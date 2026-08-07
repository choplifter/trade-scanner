import { useMemo, useState } from "react";

import { useChartFeed } from "../../hooks/useChartFeed";
import { useHistoricalBars } from "../../hooks/useHistoricalBars";
import { aggregateBars, TIMEFRAME_OPTIONS } from "../../utils/aggregateBars";
import { formatPrice } from "../../utils/format";
import { CandleChart } from "./CandleChart";

interface ChartWidgetProps {
  symbol: string | null;
}

const DEFAULT_TIMEFRAME_KEY = "5m";

export function ChartWidget({ symbol }: ChartWidgetProps) {
  const [timeframeKey, setTimeframeKey] = useState(DEFAULT_TIMEFRAME_KEY);
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
      return aggregateBars(intraday.bars, intraday.vwap, option.minutes ?? 1);
    }
    return { bars: historical.bars, vwap: historical.vwap };
  }, [option, intraday.bars, intraday.vwap, historical.bars, historical.vwap]);

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
          <CandleChart bars={displayed.bars} vwap={displayed.vwap} />
        )}
      </div>
    </div>
  );
}
