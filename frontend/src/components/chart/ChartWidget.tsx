import { useEffect, useMemo, useState } from "react";

import { useChartFeed } from "../../hooks/useChartFeed";
import type { ChartFocus } from "../../types/screener";
import { useHistoricalBars } from "../../hooks/useHistoricalBars";
import { aggregateBars, TIMEFRAME_OPTIONS } from "../../utils/aggregateBars";
import { formatPrice } from "../../utils/format";
import { CandleChart } from "./CandleChart";
import type { ChartType } from "./CandleChart";
import { SymbolInfoPanel } from "./SymbolInfoPanel";

interface ChartWidgetProps {
  symbol: string | null;
  /** Set when a backtest pick is clicked: jump to this entry and show it at
   * a resolution where it's visible. */
  focus?: ChartFocus | null;
}

const DEFAULT_TIMEFRAME_KEY = "5m";

// Which indicators the reader has switched off, by name. Persisted because
// "show me only the strategy" is a way of working, not a property of the
// symbol on screen -- it should survive a reload and a symbol change the way
// the timeframe and chart type already do.
const HIDDEN_INDICATORS_KEY = "chart:hiddenIndicators";

function loadHidden(): Set<string> {
  try {
    const raw = localStorage.getItem(HIDDEN_INDICATORS_KEY);
    return new Set<string>(raw ? JSON.parse(raw) : []);
  } catch {
    // Private browsing, storage disabled, or a value from an older shape --
    // none of which is worth failing a chart over.
    return new Set<string>();
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
  // Stored as the *hidden* set rather than the visible one so a newly added
  // indicator shows up by default instead of being invisible until someone
  // discovers a toggle for it.
  const [hiddenIndicators, setHiddenIndicators] = useState<Set<string>>(loadHidden);

  function toggleIndicator(name: string) {
    setHiddenIndicators((current) => {
      const next = new Set(current);
      if (!next.delete(name)) next.add(name);
      try {
        localStorage.setItem(HIDDEN_INDICATORS_KEY, JSON.stringify([...next]));
      } catch {
        // The toggle still works for this session; it just will not be
        // remembered next time.
      }
      return next;
    });
  }
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
  const [vwapFromPremarket, setVwapFromPremarket] = useState(false);
  // Kept across symbol and timeframe changes: how someone wants price drawn
  // is a preference, not a property of what they are looking at.
  const [chartType, setChartType] = useState<ChartType>("candles");
  const intraday = useChartFeed(symbol);
  const historical = useHistoricalBars(
    symbol,
    option.kind === "historical" ? (option.alpacaTimeframe ?? null) : null,
  );

  const displayed = useMemo(() => {
    if (option.kind === "intraday") {
      return aggregateBars(
        intraday.bars,
        vwapFromPremarket ? intraday.vwapPremarket : intraday.vwap,
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
    intraday.bars,
    intraday.vwap,
    intraday.vwapPremarket,
    vwapFromPremarket,
    intraday.indicators,
    historical.bars,
    historical.vwap,
    historical.indicators,
  ]);

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
          {option.kind === "intraday" && (
            <button
              type="button"
              className="vwap-legend"
              aria-pressed={vwapFromPremarket}
              onClick={() => setVwapFromPremarket((v) => !v)}
              title={
                vwapFromPremarket
                  ? "VWAP anchored at the premarket open, counting every print (what TradingView draws). Click for the 09:30 session anchor."
                  : "VWAP anchored at the 09:30 session open, premarket excluded. Click to anchor at the premarket open instead."
              }
            >
              <span className="vwap-swatch" /> VWAP {vwapFromPremarket ? "(pre)" : "(session)"}
            </button>
          )}
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={showIndicators}
            onClick={() => setShowIndicators((v) => !v)}
            title="Toggle the indicator lines. Individual ones can be switched off next to this."
          >
            Levels
          </button>
          {showIndicators &&
            displayed.indicators.map((indicator) => (
              <button
                key={indicator.name}
                type="button"
                className="indicator-legend"
                aria-pressed={!hiddenIndicators.has(indicator.name)}
                onClick={() => toggleIndicator(indicator.name)}
                title={`Show or hide ${indicator.name}`}
              >
                <span
                  className="indicator-swatch"
                  style={{ background: Object.values(indicator.colors ?? {})[0] ?? "#888" }}
                />
                {indicator.name}
              </button>
            ))}
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
            No trades printed for {symbol} yet today. Premarket volume is thin — this fills in
            once trades start (most reliably at 9:30 ET open).
          </div>
        ) : noHistoricalData ? (
          <div className="widget-empty">No {option.label} history available for {symbol}.</div>
        ) : (
          <CandleChart
            bars={displayed.bars}
            chartType={chartType}
            vwap={displayed.vwap}
            indicators={displayed.indicators.filter((i) => !hiddenIndicators.has(i.name))}
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
