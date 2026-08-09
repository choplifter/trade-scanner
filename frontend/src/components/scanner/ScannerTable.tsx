import { CopyButton } from "../common/CopyButton";
import type { ScannerRow } from "../../types/alpaca";
import {
  formatMarketCap,
  formatPct,
  formatPrice,
  formatRvol,
  formatShares,
  formatShortInterestPct,
  formatVolume,
  tradingViewSymbol,
} from "../../utils/format";

/** Renders "—" for fundamentals fields that are null -- either no
 * FMP_API_KEY/FINNHUB_API_KEY configured, or not fetched yet for this row. */
function cell(value: number | null, format: (value: number) => string): string {
  return value === null ? "—" : format(value);
}

interface ScannerTableProps {
  rows: ScannerRow[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

export function ScannerTable({ rows, selectedSymbol, onSelectSymbol }: ScannerTableProps) {
  if (rows.length === 0) {
    return <div className="widget-empty">No symbols matching this scanner right now.</div>;
  }

  return (
    <table className="scanner-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Last</th>
          <th>Chg %</th>
          <th>Vol</th>
          <th>RVol</th>
          <th>Float</th>
          <th>Mkt Cap</th>
          <th>Short %</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={row.symbol}
            aria-selected={row.symbol === selectedSymbol}
            onClick={() => onSelectSymbol(row.symbol)}
          >
            <td className="symbol-cell">
              {row.symbol}
              <CopyButton
                value={tradingViewSymbol(row.symbol, row.exchange)}
                title={`Copy "${tradingViewSymbol(row.symbol, row.exchange)}" to clipboard (for TradingView search)`}
              />
              {row.exchange && <span className="exchange-tag">{row.exchange}</span>}
              {row.is_hod && <span className="badge-hod">HOD</span>}
            </td>
            <td>{formatPrice(row.last_price)}</td>
            <td className={row.pct_change >= 0 ? "delta-up" : "delta-down"}>
              {formatPct(row.pct_change)}
            </td>
            <td>{formatVolume(row.volume_today)}</td>
            <td>{formatRvol(row.rvol)}</td>
            <td>{cell(row.float_shares, formatShares)}</td>
            <td>{cell(row.market_cap, formatMarketCap)}</td>
            <td>{cell(row.short_interest_pct, formatShortInterestPct)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
