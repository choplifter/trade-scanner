/** One gamma-wall strike -- see backend app.market_data.gamma_exposure.StrikeGex. */
export interface GexWall {
  strike: number;
  net_gex: number;
}

export interface GexSymbolReading {
  spot_price: number;
  as_of: string;
  net_gex: number;
  call_gex: number;
  put_gex: number;
  /** Top 5 by |net_gex|, strike-ascending -- see backend's top_walls(). */
  top_walls: GexWall[];
  call_wall: GexWall | null;
  put_wall: GexWall | null;
  /** Approximate zero-gamma crossing -- see backend's gamma_flip_strike(). */
  gamma_flip_strike: number | null;
}

/** GET /api/meta/gex -- fixed symbol list, see app.market_data.gamma_exposure.SYMBOLS. */
export interface GexResponse {
  available: boolean;
  symbols: Record<string, GexSymbolReading>;
}

/** One symbol's rule-based playbook -- see backend app.market_data.gex_plan.GexPlan. */
export interface GexPlanSymbol {
  regime: "positive" | "negative";
  near_flip: boolean;
  gamma_flip_strike: number | null;
  call_wall: GexWall | null;
  put_wall: GexWall | null;
  playbook: string[];
}

/** GET /api/meta/gex-plan -- same symbol list and availability framing as GexResponse. */
export interface GexPlanResponse {
  available: boolean;
  symbols: Record<string, GexPlanSymbol>;
}
