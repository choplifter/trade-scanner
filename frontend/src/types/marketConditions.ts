export interface VixReading {
  price: number;
  change_pct: number;
}

export interface TenYearReading {
  yield_pct: number;
  change_bp: number;
}

export interface EconomicEvent {
  date: string;
  country: string;
  event: string;
  impact: string;
}

export interface MarketConditionsResponse {
  available: boolean;
  level?: "green" | "yellow" | "red";
  reasons?: string[];
  vix?: VixReading | null;
  ten_year?: TenYearReading | null;
  high_impact_events_today?: EconomicEvent[];
  breadth_pct?: number | null;
}
