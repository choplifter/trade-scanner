export interface NewsItem {
  headline: string;
  summary: string;
  /** The article's own publisher (Benzinga, GlobeNewsWire, …) — what the
   * panel shows, since it's what tells you whether you're reading a company
   * release or a commentary piece. */
  source: string;
  url: string | null;
  published_at: string;
  /** Which feed supplied it: "alpaca" or "fmp". Provenance rather than
   * attribution; see backend app/market_data/fmp_news.py on why FMP items
   * warrant more scepticism. */
  feed: string;
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
