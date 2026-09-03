import { useCallback, useState } from "react";
import type { DragEvent } from "react";

import type { DockviewPanelApi } from "dockview-react";

import { readDroppedSymbol } from "../../utils/dragSymbol";
import { chartSymbolOf } from "../../utils/occ";
import { ChartWidget } from "../chart/ChartWidget";

interface ChartCopyPanelProps {
  api: DockviewPanelApi;
  initialSymbol: string | null;
}

export function chartCopyTitle(symbol: string | null): string {
  return symbol ? `Chart · ${chartSymbolOf(symbol)}` : "Chart";
}

/** A second chart opened from a tab's context menu: pinned to its own
 * symbol rather than following the scanner's selection. The symbol lives
 * in the panel's params (updateParameters) so the saved layout brings it
 * back, and the tab title follows it. Accepts a symbol dropped from a
 * scanner or watchlist row, like the watchlist itself. */
export function ChartCopyPanel({ api, initialSymbol }: ChartCopyPanelProps) {
  const [symbol, setSymbol] = useState<string | null>(initialSymbol);

  const change = useCallback(
    (next: string) => {
      const clean = next.trim().toUpperCase();
      setSymbol(clean || null);
      api.updateParameters({ symbol: clean || undefined });
      api.setTitle(chartCopyTitle(clean || null));
    },
    [api],
  );

  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    const dropped = readDroppedSymbol(e);
    if (!dropped) return;
    e.preventDefault();
    change(dropped);
  };

  return (
    <div className="chart-copy" onDragOver={onDragOver} onDrop={onDrop}>
      <ChartWidget symbol={symbol} onSelectSymbol={change} pinned />
    </div>
  );
}
