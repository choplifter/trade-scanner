export type ScannerHistoryHorizon = "30m" | "60m" | "latest";

export interface ScannerHistorySummaryRow {
  horizon: ScannerHistoryHorizon;
  view: string;
  sample_size: number;
  win_rate: number | null;
  avg_return: number | null;
  avg_alpha: number | null;
}

export interface ScannerHistoryPick {
  symbol: string;
  view: string;
  trading_date: string;
  first_seen_at: string;
  entry_price: number;
  entry_pct_change: number;
  entry_rvol: number;
  entry_headline: string | null;
  minutes_since_entry: number;
  current_price: number;
  pct_change_since_entry: number | null;
  benchmark_pct_change_since_entry: number | null;
  alpha_vs_benchmark: number | null;
}

export interface ScannerHistoryBucketRow {
  view: string;
  bucket: string;
  sample_size: number;
  win_rate: number;
  avg_return: number;
}

export interface ScannerHistoryResponse {
  summary: ScannerHistorySummaryRow[];
  leaderboard_best: ScannerHistoryPick[];
  leaderboard_worst: ScannerHistoryPick[];
  gap_buckets: ScannerHistoryBucketRow[];
  rvol_buckets: ScannerHistoryBucketRow[];
}
