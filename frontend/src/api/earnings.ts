/** The Earnings screen's three reads -- backend app/routers/earnings.py.
 *
 * None of them is routed through tradingPath: a reporter's chain and its
 * past moves are the same in Paper, Live and Simulation, so the mode does
 * not change the answer. Same reasoning as optionEvents in api/options.ts.
 */

import { getJson } from "./http";
import type { EarningsEvaluation, EarningsFactsResponse, EarningsScreenResponse } from "../types/earnings";

/** Who reports today and next session, liquid enough to have a chain
 * worth reading. Cheap: a cached calendar call and one batch of daily
 * bars, no option chains. */
export function earningsScreen(date?: string | null, minDollarVolume?: number | null): Promise<EarningsScreenResponse> {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (minDollarVolume != null) params.set("min_dollar_volume", String(minDollarVolume));
  const query = params.toString();
  return getJson<EarningsScreenResponse>(`/earnings/screen${query ? `?${query}` : ""}`);
}

/** The implied move and the past-report moves per symbol. One expiries
 * call and one chain read each, so this is the slow half of the list --
 * the table renders without it and fills the columns in when it lands.
 * At most 30 symbols per call (the backend rejects more). */
export function earningsFacts(symbols: string[]): Promise<EarningsFactsResponse> {
  return getJson<EarningsFactsResponse>(`/earnings/facts?symbols=${encodeURIComponent(symbols.join(","))}`);
}

/** One reporter's signals, the families scored against them, and the
 * picked ones priced. Several seconds: two chains, the GEX profile and up
 * to a dozen previews. Read-only -- every structure comes back as a
 * ticket the user still submits by hand. */
export function earningsEvaluate(symbol: string): Promise<EarningsEvaluation> {
  return getJson<EarningsEvaluation>(`/earnings/evaluate/${encodeURIComponent(symbol)}`);
}
