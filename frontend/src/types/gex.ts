/** One gamma-wall strike -- see backend app.market_data.gamma_exposure.StrikeGex. */
export interface GexWall {
  strike: number;
  net_gex: number;
}

export interface GexSymbolReading {
  spot_price: number;
  as_of: string;
  /** Top 3 by |net_gex|, strike-ascending -- see backend's top_walls(). */
  top_walls: GexWall[];
}

/** GET /api/meta/gex -- SPY/QQQ only, see app.market_data.gamma_exposure.SYMBOLS. */
export interface GexResponse {
  available: boolean;
  symbols: Record<string, GexSymbolReading>;
}
