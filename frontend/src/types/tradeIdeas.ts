export interface TradeIdea {
  symbol: string;
  exchange: string;
  headline: string;
  reason: string;
  signal_score: number;
}

export interface TradeIdeasResponse {
  ideas: TradeIdea[];
  disclaimer: string;
}

export interface TradeIdeaPerformance {
  symbol: string;
  headline: string;
  generated_at: string;
  minutes_since: number;
  entry_price: number;
  current_price: number | null;
  pct_change_since_pick: number | null;
}

export interface TradeIdeasPerformanceResponse {
  picks: TradeIdeaPerformance[];
}
