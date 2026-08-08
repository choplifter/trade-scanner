import type { ScannerRow } from "../types/alpaca";

const MOCK_POOL: { symbol: string; exchange: string; basePrice: number; avgVol: number }[] = [
  { symbol: "GMEX", exchange: "NASDAQ", basePrice: 4.2, avgVol: 3_000_000 },
  { symbol: "BIOX", exchange: "NYSE", basePrice: 12.5, avgVol: 900_000 },
  { symbol: "VOLT", exchange: "NASDAQ", basePrice: 2.0, avgVol: 6_500_000 },
  { symbol: "RKTY", exchange: "AMEX", basePrice: 0.85, avgVol: 4_100_000 },
  { symbol: "SPRN", exchange: "NYSE", basePrice: 55.0, avgVol: 500_000 },
  { symbol: "QBIT", exchange: "NASDAQ", basePrice: 18.5, avgVol: 700_000 },
  { symbol: "NRGY", exchange: "ARCA", basePrice: 3.3, avgVol: 800_000 },
  { symbol: "DRNE", exchange: "NASDAQ", basePrice: 7.0, avgVol: 1_200_000 },
  { symbol: "HALO", exchange: "NASDAQ", basePrice: 24.1, avgVol: 1_500_000 },
  { symbol: "CRSP", exchange: "NASDAQ", basePrice: 41.3, avgVol: 2_200_000 },
  { symbol: "TIDE", exchange: "NYSE", basePrice: 9.6, avgVol: 1_000_000 },
  { symbol: "PLSM", exchange: "AMEX", basePrice: 1.4, avgVol: 3_800_000 },
];

function randomBetween(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

/**
 * Randomized, plausible-looking scanner rows for exercising the UI without
 * live market data (e.g. testing on a weekend). Re-call on an interval to
 * simulate a live-updating feed.
 */
export function generateMockRows(): ScannerRow[] {
  const now = new Date().toISOString();

  const rows = MOCK_POOL.map(({ symbol, exchange, basePrice, avgVol }) => {
    const pctChange = randomBetween(-8, 140);
    const prevClose = basePrice;
    const lastPrice = prevClose * (1 + pctChange / 100);
    const volumeToday = avgVol * randomBetween(0.3, 30);

    const isHod = Math.random() > 0.65;
    const dayHigh = isHod ? lastPrice : lastPrice * randomBetween(1.01, 1.08);
    const isLod = !isHod && Math.random() > 0.85;
    const dayLow = isLod ? lastPrice : lastPrice * randomBetween(0.85, 0.99);

    const row: ScannerRow = {
      symbol,
      exchange,
      last_price: lastPrice,
      prev_close: prevClose,
      pct_change: pctChange,
      volume_today: volumeToday,
      avg_vol_20d: avgVol,
      rvol: volumeToday / avgVol,
      dollar_volume_today: volumeToday * lastPrice,
      day_high: dayHigh,
      day_low: dayLow,
      is_hod: isHod,
      is_lod: isLod,
      updated_at: now,
    };
    return row;
  });

  return rows.sort((a, b) => b.pct_change - a.pct_change);
}
