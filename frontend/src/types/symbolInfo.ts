export interface NewsItem {
  headline: string;
  summary: string;
  source: string;
  url: string | null;
  published_at: string;
}

export interface SymbolInfoResponse {
  symbol: string;
  company_name: string | null;
  sector: string | null;
  industry: string | null;
  description: string | null;
  website: string | null;
  logo_url: string | null;
  market_cap: number | null;
  float_shares: number | null;
  news: NewsItem[];
}
