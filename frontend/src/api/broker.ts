/** /api/broker/* -- connect, check and disconnect the user's own Alpaca
 * accounts (backend app.routers.broker). Not routed through tradingPath():
 * the keys are per user, not per trading mode. */

import { API_BASE, OrderRejectedError, checkUnauthorized, extractErrorMessage, getJson } from "./http";
import type { BrokerAccountStatus, BrokerStatusResponse } from "../types/broker";
import type { TradingAccount, TradingRejection } from "../types/trading";

export function getBrokerStatus(): Promise<BrokerStatusResponse> {
  return getJson<BrokerStatusResponse>("/broker/status");
}

async function send<T>(method: "POST" | "DELETE", path: string, body?: unknown, confirm?: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
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
    const payload = (await res.json()) as { detail: TradingRejection | { msg?: string; loc?: unknown[] }[] | string };
    if (Array.isArray(payload.detail)) {
      const first = payload.detail[0];
      const loc = Array.isArray(first?.loc) ? first.loc.filter((x) => x !== "body").join(".") : "";
      throw new OrderRejectedError({
        code: "validation_error",
        message: first?.msg?.replace(/^Value error, /, "") ?? "Invalid request",
        field: loc || null,
      });
    }
    if (typeof payload.detail === "string") {
      throw new OrderRejectedError({ code: "validation_error", message: payload.detail, field: null });
    }
    throw new OrderRejectedError(payload.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `${method} ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

/** Verifies the pair against Alpaca and stores it. A live pair needs the
 * typed LIVE confirmation like every real-money action. */
export function connectBroker(
  account: TradingAccount,
  apiKeyId: string,
  apiSecretKey: string,
  confirm?: string,
): Promise<{ status: BrokerAccountStatus }> {
  return send<{ status: BrokerAccountStatus }>(
    "POST",
    `/broker/${account}`,
    { api_key_id: apiKeyId, api_secret_key: apiSecretKey },
    confirm,
  );
}

export function disconnectBroker(account: TradingAccount): Promise<{ status: BrokerAccountStatus; removed: boolean }> {
  return send<{ status: BrokerAccountStatus; removed: boolean }>("DELETE", `/broker/${account}`);
}
