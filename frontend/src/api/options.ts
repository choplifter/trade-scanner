/**
 * /api/trading/options/* -- routed through tradingPath() like every other
 * trading call, so Paper, Live and Simulation land on their own prefix.
 * Simulation is served by the local options book (/trading/sim/options,
 * backend app.trading.sim.options_service) with the same endpoints plus
 * the two a local book needs: its resting packages and their cancel.
 */

import { API_BASE, OrderRejectedError, checkUnauthorized, extractErrorMessage, getJson } from "./http";
import { tradingPath } from "./tradingMode";
import type {
  ChainResponse,
  Payoff,
  PayoffRequest,
  LegQuote,
  ClosePreview,
  CloseSpreadRequest,
  ExpiriesResponse,
  OptionsAccountResponse,
  OrderResponse,
  SpreadPreview,
  SpreadTicketRequest,
  SpreadsResponse,
  TriggerCreateRequest,
  OptionsIdeaResponse,
  OptimizeRequest,
  OptimizeResponse,
  OptionEventsResponse,
  RollPreview,
  RollRequest,
  RollResponse,
  UnderlyingTrigger,
} from "../types/options";
import type { Order, TradingRejection } from "../types/trading";

/** POST/DELETE with the trading-rejection convention: a 422 carries a typed
 * detail the widget renders through one path. `confirm` is the typed LIVE
 * from a live-mode dialog, sent as the header the backend checks. */
async function send<T>(method: "POST" | "DELETE", path: string, body?: unknown, confirm?: string): Promise<T> {
  const res = await fetch(`${API_BASE}${tradingPath(path)}`, {
    method,
    credentials: "include",
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(confirm ? { "X-Live-Confirm": confirm } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  checkUnauthorized(res);
  if (res.status === 422) {
    const body = (await res.json()) as { detail: TradingRejection | { msg?: string; loc?: unknown[] }[] | string };
    // A pydantic validation error arrives as a list; surface its first
    // message rather than an empty rejection.
    if (Array.isArray(body.detail)) {
      const first = body.detail[0];
      const loc = Array.isArray(first?.loc) ? first.loc.filter((x) => x !== "body").join(".") : "";
      throw new OrderRejectedError({
        code: "validation_error",
        message: first?.msg?.replace(/^Value error, /, "") ?? "Invalid request",
        field: loc || null,
      });
    }
    if (typeof body.detail === "string") {
      throw new OrderRejectedError({ code: "validation_error", message: body.detail, field: null });
    }
    throw new OrderRejectedError(body.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `${method} ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

export function getOptionsAccount(): Promise<OptionsAccountResponse> {
  return getJson<OptionsAccountResponse>(tradingPath("/trading/options/account"));
}

/** The expiry strip. `board` appends every listed expiry beyond it (out to
 * about three years, from the strikes nearest the spot -- what the expiry
 * axis draws); `far` = {from, to} in days out appends the expiries in that
 * window with their full contract counts (a LEAPS ticket, a long call's
 * roll). */
export function getExpiries(underlying: string, opts: { far?: { from: number; to: number }; board?: boolean } = {}): Promise<ExpiriesResponse> {
  const params = new URLSearchParams();
  if (opts.far) {
    params.set("far_from", String(Math.max(0, Math.floor(opts.far.from))));
    params.set("far_to", String(Math.max(0, Math.floor(opts.far.to))));
  }
  if (opts.board) params.set("board", "true");
  const query = params.size ? `?${params.toString()}` : "";
  return getJson<ExpiriesResponse>(tradingPath(`/trading/options/expiries/${encodeURIComponent(underlying)}${query}`));
}

/** Days from today to an ISO date (calendar days, local midnight). */
export function daysUntil(iso: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const [y, m, d] = iso.split("-").map(Number);
  return Math.round((new Date(y, m - 1, d).getTime() - today.getTime()) / 86_400_000);
}

export function getContractQuote(symbol: string): Promise<LegQuote> {
  return getJson<LegQuote>(tradingPath(`/trading/options/contract/${encodeURIComponent(symbol)}`));
}

export function getChain(underlying: string, expiry: string): Promise<ChainResponse> {
  return getJson<ChainResponse>(
    tradingPath(`/trading/options/chain/${encodeURIComponent(underlying)}?expiry=${encodeURIComponent(expiry)}`),
  );
}

export function previewSpread(ticket: SpreadTicketRequest): Promise<SpreadPreview> {
  return send<SpreadPreview>("POST", "/trading/options/preview", ticket);
}

export function submitSpread(ticket: SpreadTicketRequest, confirm?: string): Promise<OrderResponse> {
  return send<OrderResponse>("POST", "/trading/options/orders", ticket, confirm);
}

export function getSpreads(): Promise<SpreadsResponse> {
  return getJson<SpreadsResponse>(tradingPath("/trading/options/spreads"));
}

export function previewCloseSpread(body: CloseSpreadRequest): Promise<ClosePreview> {
  return send<ClosePreview>("POST", "/trading/options/spreads/close/preview", body);
}

export function getSpreadPayoff(body: PayoffRequest): Promise<Payoff> {
  return send<Payoff>("POST", "/trading/options/spreads/payoff", body);
}

/** The account's option orders: the simulated book's packages in
 * Simulation, Alpaca's option orders in Paper and Live -- resting ones by
 * default, what the Open spreads tab lists as Working packages. */
export function getOptionOrders(status: "open" | "closed" | "all" = "open"): Promise<{ orders: Order[] }> {
  return getJson<{ orders: Order[] }>(tradingPath(`/trading/options/orders?status=${status}`));
}

export function cancelOptionOrder(id: string, confirm?: string): Promise<{ cancelled: string }> {
  return send<{ cancelled: string }>("DELETE", `/trading/options/orders/${encodeURIComponent(id)}`, undefined, confirm);
}

export function closeSpread(body: CloseSpreadRequest, confirm?: string): Promise<OrderResponse> {
  return send<OrderResponse>("POST", "/trading/options/spreads/close", body, confirm);
}

/** Both halves of a roll priced together with the net per package. */
export function previewRoll(body: RollRequest): Promise<RollPreview> {
  return send<RollPreview>("POST", "/trading/options/spreads/roll/preview", body);
}

/** Close a held leg and open its replacement: one package in the
 * simulation (both legs or neither), two orders in sequence at Alpaca. */
export function rollSpread(body: RollRequest, confirm?: string): Promise<RollResponse> {
  return send<RollResponse>("POST", "/trading/options/spreads/roll", body, confirm);
}

export function getTriggers(): Promise<{ triggers: UnderlyingTrigger[] }> {
  return getJson<{ triggers: UnderlyingTrigger[] }>(tradingPath("/trading/options/triggers"));
}

export function createTrigger(body: TriggerCreateRequest, confirm?: string): Promise<{ trigger: UnderlyingTrigger }> {
  return send<{ trigger: UnderlyingTrigger }>("POST", "/trading/options/triggers", body, confirm);
}

export function deleteTrigger(id: string): Promise<{ cancelled: string }> {
  return send<{ cancelled: string }>("DELETE", `/trading/options/triggers/${encodeURIComponent(id)}`);
}

/** Ask Claude for option structures on this underlying. Slow by nature --
 * it loads three expiries of chain, gathers the context around them and
 * then reasons over it -- so callers need a real pending state, not a
 * spinner that flashes. Read-only: nothing is ordered, and the ideas come
 * back with a ready-made ticket the user still submits by hand. */
export function suggestOptionsIdeas(underlying: string): Promise<OptionsIdeaResponse> {
  return send<OptionsIdeaResponse>("POST", "/trading/options/idea", { underlying });
}

/** Structures for a price target on a horizon date, enumerated from the
 * listed chain and priced through the ticket's own path -- see backend
 * app/options/optimize.py. A few seconds: three expiries of chain, a
 * thousand candidates priced, a dozen previewed. Read-only. */
export function optimizeStructures(body: OptimizeRequest): Promise<OptimizeResponse> {
  return send<OptimizeResponse>("POST", "/trading/options/optimize", body);
}

/** Earnings (with the stock's past moves), macro releases and IV rank for
 * an underlying -- backend app/options/events.py. Not routed through
 * tradingPath: nothing here depends on the account or the mode, and the
 * simulation's chain is the same symbol's. `atmIv` is the chain's own
 * at-the-money IV, which the rank is measured against. */
export function optionEvents(underlying: string, atmIv: number | null, dte: number | null): Promise<OptionEventsResponse> {
  const params = new URLSearchParams();
  if (atmIv != null && atmIv > 0) params.set("atm_iv", String(atmIv));
  if (dte != null && dte > 0) params.set("dte", String(dte));
  const query = params.toString();
  return getJson<OptionEventsResponse>(`/trading/options/events/${encodeURIComponent(underlying)}${query ? `?${query}` : ""}`);
}
