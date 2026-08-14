import type { AlarmEntry } from "../../hooks/useAlarms";
import { formatPct, formatPrice } from "../../utils/format";

interface AlarmsOverlayProps {
  open: boolean;
  alarms: AlarmEntry[];
  onClose: () => void;
  onSelectSymbol: (symbol: string) => void;
}

export function AlarmsOverlay({ open, alarms, onClose, onSelectSymbol }: AlarmsOverlayProps) {
  if (!open || alarms.length === 0) return null;

  return (
    <div className="alarms-backdrop" onClick={onClose}>
      <div className="alarms-panel" onClick={(e) => e.stopPropagation()}>
        <div className="alarms-panel-header">
          <h2>Momentum Alarms</h2>
          <button type="button" className="alarms-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <p className="alarms-panel-subtitle">
          Fast, wick-less moves -- a large 15-minute price change with almost no pullback candle.
        </p>
        <ul className="alarms-list">
          {alarms.map(({ row, firstSeenAt }) => (
            <li
              key={row.symbol}
              className="alarms-list-item"
              onClick={() => {
                onSelectSymbol(row.symbol);
                onClose();
              }}
            >
              <span className="alarms-symbol">{row.symbol}</span>
              <span className="alarms-price">{formatPrice(row.last_price)}</span>
              <span className={(row.pct_change_last_15m ?? 0) >= 0 ? "delta-up" : "delta-down"}>
                {row.pct_change_last_15m === null ? "—" : formatPct(row.pct_change_last_15m)} / 15m
              </span>
              <span className="alarms-time">
                {new Date(firstSeenAt).toLocaleTimeString(undefined, {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
