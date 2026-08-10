import { useEffect, useRef, useState } from "react";

import type { ScannerRow } from "../../types/alpaca";
import { formatPct, formatPrice, formatVolume } from "../../utils/format";
import { HEATMAP_STRONG_THRESHOLD, heatmapBlendWeight, heatmapFill } from "../../utils/heatmapColor";
import { computeTreemap } from "../../utils/treemap";

interface ScannerHeatmapProps {
  rows: ScannerRow[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

// Below this, a tile's own symbol/% text would either overflow or be
// unreadably small -- it still renders (as a plain colored block, hoverable
// via its native title tooltip) just without the inline label. See
// marks-and-anatomy's "a label that won't fit doesn't get clipped" rule.
const MIN_LABEL_WIDTH = 40;
const MIN_LABEL_HEIGHT = 26;

/** Scanner rows as an area-proportional treemap: tile size = dollar volume
 * traded today, tile color = %-change (diverging red/green off the app's
 * own --delta-up/--delta-down tokens), same encoding as the Dash analytics
 * app's Plotly treemap (scanner_heatmap.py) but native to this app's design
 * system instead of pulling in a charting library for one widget. */
export function ScannerHeatmap({ rows, selectedSymbol, onSelectSymbol }: ScannerHeatmapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  if (rows.length === 0) {
    return <div className="widget-empty">No symbols matching this scanner right now.</div>;
  }

  const maxAbsPct = Math.max(...rows.map((r) => Math.abs(r.pct_change)), 1);
  const tiles = computeTreemap(
    rows.map((r) => ({ ...r, value: r.dollar_volume_today })),
    size.width,
    size.height,
  );

  return (
    <div className="heatmap-wrap">
      <div className="heatmap-container" ref={containerRef}>
        {tiles.map((tile) => {
          const weight = heatmapBlendWeight(tile.pct_change, maxAbsPct);
          const showLabel = tile.w >= MIN_LABEL_WIDTH && tile.h >= MIN_LABEL_HEIGHT;
          const classes = [
            "heatmap-tile",
            weight > HEATMAP_STRONG_THRESHOLD ? "heatmap-tile-strong" : "",
            tile.symbol === selectedSymbol ? "heatmap-tile-selected" : "",
          ]
            .filter(Boolean)
            .join(" ");

          return (
            <div
              key={tile.symbol}
              className={classes}
              style={{
                left: tile.x + 1,
                top: tile.y + 1,
                width: Math.max(0, tile.w - 2),
                height: Math.max(0, tile.h - 2),
                background: heatmapFill(tile.pct_change, maxAbsPct),
              }}
              onClick={() => onSelectSymbol(tile.symbol)}
              title={`${tile.symbol} · ${formatPrice(tile.last_price)} · ${formatPct(tile.pct_change)} · Vol ${formatVolume(tile.volume_today)}`}
            >
              {showLabel && (
                <>
                  <span className="heatmap-tile-symbol">{tile.symbol}</span>
                  <span className="heatmap-tile-pct">{formatPct(tile.pct_change)}</span>
                </>
              )}
            </div>
          );
        })}
      </div>
      <p className="heatmap-caption">Tile size = $ volume traded today · Color = % change</p>
    </div>
  );
}
