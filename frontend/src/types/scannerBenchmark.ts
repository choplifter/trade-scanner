export interface ScannerBenchmarkPick {
  symbol: string;
  view: string;
  first_seen_at: string;
  minutes_since: number;
  entry_price: number;
  entry_pct_change: number;
  entry_rvol: number;
  entry_headline: string | null;
  /** Today's headline as it stands now. entry_headline is a frozen
   * point-in-time snapshot and is usually null -- most symbols are first
   * flagged premarket, before the day's story exists. */
  current_headline: string | null;
  current_price: number | null;
  pct_change_since_entry: number | null;
  benchmark_pct_change_since_entry: number | null;
  alpha_vs_benchmark: number | null;
}

export interface ScannerBenchmarkResponse {
  benchmark_symbol: string;
  picks: ScannerBenchmarkPick[];
}
