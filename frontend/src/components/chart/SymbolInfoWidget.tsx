import { useSymbolInfoContext } from "../../context/SymbolInfoContext";
import { SymbolInfoPanel } from "./SymbolInfoPanel";

interface SymbolInfoWidgetProps {
  symbol: string | null;
}

/** Company profile + recent news for the symbol on screen -- split out of
 * ChartWidget into its own widget so the chart's own box is candles-only
 * (see useDashboardLayout.ts's DEFAULT_LAYOUT comment on why `chart` used
 * to need extra height). Reads the shared fetch off SymbolInfoContext
 * rather than calling useSymbolInfo itself, since ChartWidget already fetches
 * it (for the chart's own news-pin markers) and a second call here would
 * double-fetch the same payload. */
export function SymbolInfoWidget({ symbol }: SymbolInfoWidgetProps) {
  const { symbolInfo, highlightedNews } = useSymbolInfoContext();

  return (
    <div className="widget symbol-info-widget">
      <div className="widget-header">
        <h2>{symbol ?? "Symbol Info"}</h2>
      </div>
      <div className="widget-body">
        {symbol ? (
          <SymbolInfoPanel symbol={symbol} state={symbolInfo} highlightTimes={highlightedNews} />
        ) : (
          <div className="widget-empty">Click a symbol in a scanner to see company info and news.</div>
        )}
      </div>
    </div>
  );
}
