export function formatPrice(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

export function formatVolume(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toFixed(0);
}

export function formatRvol(value: number): string {
  return `${value.toFixed(2)}x`;
}

export function formatShares(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toFixed(0);
}

export function formatMarketCap(value: number): string {
  if (value >= 1_000_000_000_000) return `$${(value / 1_000_000_000_000).toFixed(2)}T`;
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  return `$${value.toFixed(0)}`;
}

/** Same scale/precision as formatMarketCap -- kept as its own named
 * function since dollar volume and market cap are different metrics that
 * just happen to format the same way. */
export function formatDollarVolume(value: number): string {
  return formatMarketCap(value);
}

export function formatShortInterestPct(value: number): string {
  return `${value.toFixed(2)}%`;
}

/** TradingView's unambiguous symbol format -- falls back to the bare symbol
 * when the exchange isn't known. */
export function tradingViewSymbol(symbol: string, exchange: string | undefined | null): string {
  return exchange ? `${exchange}:${symbol}` : symbol;
}
