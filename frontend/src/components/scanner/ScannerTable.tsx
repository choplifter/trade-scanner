import { useMemo, useState } from "react";

import { CopyButton } from "../common/CopyButton";
import type { ScannerRow } from "../../types/alpaca";
import {
  formatDollarVolume,
  formatMarketCap,
  formatPct,
  formatPrice,
  formatRvol,
  formatShares,
  formatShortInterestPct,
  tradingViewSymbol,
} from "../../utils/format";

/** Renders "—" for fundamentals fields that are null -- either no
 * FMP_API_KEY configured, or not fetched yet for this row. */
function cell(value: number | null, format: (value: number) => string): string {
  return value === null ? "—" : format(value);
}

function stringCell(value: string | null): string {
  return value ? value : "—";
}

type SortKey =
  | "symbol"
  | "company_name"
  | "last"
  | "chg"
  | "momentum"
  | "vol"
  | "rvol"
  | "rvol_1h"
  | "float"
  | "market_cap"
  | "short_interest_pct"
  | "exchange"
  | "country"
  | "sector";

type SortValue = string | number | null;

/** Sorts on the real underlying ScannerRow value, not the formatted
 * display string -- same reasoning as the Dash table's _COLUMN_SORT_KEYS:
 * "1.25B" < "250.0M" lexicographically even though 1.25B is the bigger
 * number, so every formatted column needs its own accessor here rather
 * than a native string sort. */
const SORT_ACCESSORS: Record<SortKey, (row: ScannerRow) => SortValue> = {
  symbol: (r) => r.symbol,
  company_name: (r) => r.company_name,
  last: (r) => r.last_price,
  chg: (r) => r.pct_change,
  momentum: (r) => r.momentum_pct,
  vol: (r) => r.dollar_volume_today,
  rvol: (r) => r.rvol,
  rvol_1h: (r) => r.rvol_1h,
  float: (r) => r.float_shares,
  market_cap: (r) => r.market_cap,
  short_interest_pct: (r) => r.short_interest_pct,
  exchange: (r) => r.exchange,
  country: (r) => r.country,
  sector: (r) => r.sector,
};

/** The window column is the one label that isn't fixed: it reports whatever
 * trailing window the screen asked for (Screen.window_minutes), so calling it
 * "1h" was a lie the moment that became configurable. */
function columns(
  windowMinutes: number,
  momentumWindowMinutes: number,
): { id: SortKey; label: string }[] {
  return [
    { id: "symbol", label: "Symbol" },
    { id: "company_name", label: "Company" },
    { id: "last", label: "Last" },
    { id: "chg", label: "Chg %" },
    {
      id: "momentum",
      label: momentumWindowMinutes ? `${momentumWindowMinutes}m %` : "Mom %",
    },
    { id: "vol", label: "$ Vol" },
    { id: "rvol", label: "RVol" },
    { id: "rvol_1h", label: windowMinutes ? `RVol ${windowMinutes}m` : "RVol (win)" },
    { id: "float", label: "Float" },
    { id: "market_cap", label: "Mkt Cap" },
    { id: "short_interest_pct", label: "Short %" },
    { id: "exchange", label: "Exchange" },
    { id: "country", label: "Country" },
    { id: "sector", label: "Sector" },
  ];
}

interface SortState {
  key: SortKey;
  direction: "asc" | "desc";
}

/** Rows with a null value for the sort column always sort to the bottom
 * regardless of direction -- comparing null against a real value isn't
 * meaningful, and burying unknowns matches how the "—" cell reads (same
 * behavior as the Dash table's _sorted_rows). */
function sortRows(rows: ScannerRow[], sort: SortState): ScannerRow[] {
  const accessor = SORT_ACCESSORS[sort.key];
  const withValue: ScannerRow[] = [];
  const withoutValue: ScannerRow[] = [];
  for (const row of rows) {
    (accessor(row) === null ? withoutValue : withValue).push(row);
  }
  const dir = sort.direction === "asc" ? 1 : -1;
  withValue.sort((a, b) => {
    const av = accessor(a);
    const bv = accessor(b);
    if (typeof av === "string" || typeof bv === "string") {
      return String(av).localeCompare(String(bv)) * dir;
    }
    return ((av as number) - (bv as number)) * dir;
  });
  return [...withValue, ...withoutValue];
}

interface ScannerTableProps {
  rows: ScannerRow[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
  /** Derived columns from the screen response. rvol_window lives here rather
   * than on the row because it depends on the screen's window, not on the
   * global setting row.rvol_1h was built with. */
  derived?: Record<string, Record<string, number | null>>;
  windowMinutes?: number;
  momentumWindowMinutes?: number;
}

export function ScannerTable({
  rows,
  selectedSymbol,
  onSelectSymbol,
  derived,
  windowMinutes = 0,
  momentumWindowMinutes = 0,
}: ScannerTableProps) {
  const [sort, setSort] = useState<SortState | null>(null);
  const COLUMNS = useMemo(
    () => columns(windowMinutes, momentumWindowMinutes),
    [windowMinutes, momentumWindowMinutes],
  );
  /** The screen's own windowed value where there is one, the row's global
   * one otherwise -- so the number always matches the header above it. */
  const windowedRvol = (row: ScannerRow) =>
    derived?.rvol_window?.[row.symbol] ?? row.rvol_1h;

  const sortedRows = useMemo(() => (sort ? sortRows(rows, sort) : rows), [rows, sort]);

  function toggleSort(key: SortKey) {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, direction: "asc" };
      return prev.direction === "asc" ? { key, direction: "desc" } : null;
    });
  }

  if (rows.length === 0) {
    return <div className="widget-empty">No symbols matching this scanner right now.</div>;
  }

  return (
    <table className="scanner-table">
      <thead>
        <tr>
          {COLUMNS.map((col) => {
            const active = sort?.key === col.id;
            return (
              <th
                key={col.id}
                className="sortable-header"
                aria-sort={active ? (sort!.direction === "asc" ? "ascending" : "descending") : "none"}
                onClick={() => toggleSort(col.id)}
              >
                {col.label}
                <span className="sort-indicator">{active ? (sort!.direction === "asc" ? " ▲" : " ▼") : ""}</span>
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {sortedRows.map((row) => (
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
              {row.is_stale && (
                <span
                  className="badge-stale"
                  title="No confirmed trade recently -- this price may be older than it looks"
                >
                  STALE
                </span>
              )}
              {row.shortable && (
                <span
                  className="badge-shortable"
                  title="Shortable -- the broker lists this symbol as available to sell short. A static flag, not a live locate: the borrow can still fail at order time."
                >
                  SHORT OK
                </span>
              )}
              {row.is_fade_risk && (
                <span
                  className="badge-fade-risk"
                  title="RVol >15x -- historically more often a blow-off/exhaustion move than a continuation (this app's own scanner history shows a lower win rate at this RVol range)"
                >
                  FADE RISK
                </span>
              )}
              {row.is_momentum_alert && (
                <span
                  className="badge-momentum-alert"
                  title="Fast bullish move -- a large gain over the momentum window confirmed by a green, wick-less candle trading above VWAP"
                >
                  ⚡ MOMENTUM
                </span>
              )}
              {row.recent_headline && (
                <span className="badge-news" title={row.recent_headline}>
                  📰
                </span>
              )}
            </td>
            <td className="company-cell" title={row.company_name ?? undefined}>
              {stringCell(row.company_name)}
            </td>
            <td>{formatPrice(row.last_price)}</td>
            <td className={row.pct_change >= 0 ? "delta-up" : "delta-down"}>
              {formatPct(row.pct_change)}
            </td>
            <td
              className={
                row.momentum_pct === null
                  ? undefined
                  : row.momentum_pct >= 0
                    ? "delta-up"
                    : "delta-down"
              }
              title="Price change over just the momentum window -- distinct from Chg %, which is since prior close"
            >
              {cell(row.momentum_pct, formatPct)}
            </td>
            <td>{formatDollarVolume(row.dollar_volume_today)}</td>
            <td>{formatRvol(row.rvol)}</td>
            <td title="Volume in the last hour vs. what this symbol normally trades in that same clock hour -- a rate, unlike RVol, which is cumulative since the open and only ever climbs">
              {cell(windowedRvol(row), formatRvol)}
            </td>
            <td>{cell(row.float_shares, formatShares)}</td>
            <td>{cell(row.market_cap, formatMarketCap)}</td>
            <td>{cell(row.short_interest_pct, formatShortInterestPct)}</td>
            <td>{stringCell(row.exchange)}</td>
            <td>{stringCell(row.country)}</td>
            <td>{stringCell(row.sector)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
