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

/** The nearest expiry's own gamma profile -- today's while it trades --
 * see backend gamma_exposure.NearExpiryGex. `source` is "solved" when at
 * least one gamma had to be solved from the contract's quote (Alpaca
 * computes none for a contract expiring today). */
export interface NearExpiryGex extends GexSupport {
  expiry: string;
  dte: number;
  is_today: boolean;
  source: "alpaca" | "solved";
  net_gex: number;
  top_walls: GexWall[];
  call_wall: GexWall | null;
  put_wall: GexWall | null;
  gamma_flip_strike: number | null;
}

/** The straddle-implied move to the nearest expiry -- see backend
 * gamma_exposure.ExpectedMove. `move` is the ATM straddle's mid (the
 * market's expected absolute move); `one_sigma` = move * sqrt(pi/2). */
export interface ExpectedMove {
  expiry: string;
  dte: number;
  strike: number;
  straddle_mid: number;
  move: number;
  one_sigma: number;
  low: number;
  high: number;
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
  near?: NearExpiryGex | null;
  expected_move?: ExpectedMove | null;
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
  near?: NearExpiryGex | null;
  expected_move?: ExpectedMove | null;
}

/** GET /api/meta/gex-plan -- same on-demand `symbol` behaviour and
 * availability framing as GexResponse. */
export interface GexPlanResponse {
  available: boolean;
  symbols: Record<string, GexPlanSymbol>;
}
