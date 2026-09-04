/** One gamma-wall strike -- see backend app.market_data.gamma_exposure.StrikeGex. */
export interface GexWall {
  strike: number;
  net_gex: number;
}

/** What a reading rests on. On a thinly-traded chain a "gamma wall" can be
 * a handful of contracts, so the numbers come with their own sample size
 * rather than being suppressed by a liquidity threshold -- see the backend
 * note in app/market_data/gex_cache.py. */
export interface GexSupport {
  /** Strikes that carried usable greeks. */
  contracts_used: number;
  /** Total open interest across them. */
  open_interest_used: number;
}

export interface GexSymbolReading extends GexSupport {
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

/** GET /api/meta/gex. Without `symbol` every reading currently held; with
 * one, that symbol alone, computed on demand -- any optionable ticker, not
 * a fixed list. */
export interface GexResponse {
  available: boolean;
  symbols: Record<string, GexSymbolReading>;
}

/** One symbol's rule-based playbook -- see backend app.market_data.gex_plan.GexPlan. */
export interface GexPlanSymbol extends GexSupport {
  regime: "positive" | "negative";
  near_flip: boolean;
  gamma_flip_strike: number | null;
  call_wall: GexWall | null;
  put_wall: GexWall | null;
  playbook: string[];
}

/** GET /api/meta/gex-plan -- same on-demand `symbol` behaviour and
 * availability framing as GexResponse. */
export interface GexPlanResponse {
  available: boolean;
  symbols: Record<string, GexPlanSymbol>;
}
