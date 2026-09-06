/**
 * /api/trading/options/playbooks/* -- the campaigns of the playbook scripts.
 * Not routed through tradingPath: the endpoints take the account explicitly
 * (Simulation only for now), and a campaign is the same object whichever
 * mode the dashboard is showing.
 */

import { API_BASE, OrderRejectedError, checkUnauthorized, extractErrorMessage, getJson } from "./http";
import type { BacktestRequest, BacktestResult, Campaign, CampaignCreate, CampaignPatch, PlaybookScriptsResponse } from "../types/playbooks";
import type { TradingRejection } from "../types/trading";

const BASE = "/trading/options/playbooks";

async function send<T>(method: "POST" | "PATCH", path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  checkUnauthorized(res);
  if (!res.ok) {
    // A 422 carries the same typed detail the trading routers use.
    if (res.status === 422) {
      try {
        const payload = (await res.clone().json()) as { detail?: unknown };
        const detail = payload.detail;
        if (detail && typeof detail === "object" && "message" in (detail as Record<string, unknown>)) {
          throw new OrderRejectedError(detail as TradingRejection);
        }
      } catch (err) {
        if (err instanceof OrderRejectedError) throw err;
      }
    }
    throw new Error(await extractErrorMessage(res, `Request failed (${res.status})`));
  }
  return (await res.json()) as T;
}

export function getPlaybookScripts(): Promise<PlaybookScriptsResponse> {
  return getJson<PlaybookScriptsResponse>(`${BASE}/scripts`);
}

export function getCampaigns(account: "sim", includeClosed = false): Promise<{ campaigns: Campaign[] }> {
  return getJson<{ campaigns: Campaign[] }>(`${BASE}/campaigns?account=${account}&include_closed=${includeClosed}`);
}

export function createCampaign(body: CampaignCreate): Promise<Campaign> {
  return send<Campaign>("POST", `${BASE}/campaigns`, body);
}

export function patchCampaign(id: string, body: CampaignPatch): Promise<Campaign> {
  return send<Campaign>("PATCH", `${BASE}/campaigns/${encodeURIComponent(id)}`, body);
}

export function proposeNow(id: string): Promise<Campaign> {
  return send<Campaign>("POST", `${BASE}/campaigns/${encodeURIComponent(id)}/propose`);
}

export function addCampaignNote(id: string, note: string): Promise<Campaign> {
  return send<Campaign>("POST", `${BASE}/campaigns/${encodeURIComponent(id)}/note`, { note });
}

/** Walk a playbook over months of daily closes with synthetic
 * (Black-Scholes) chains -- how the rules behave, not what they earned. */
export function runPlaybookBacktest(body: BacktestRequest): Promise<BacktestResult> {
  return send<BacktestResult>("POST", `${BASE}/backtest`, body);
}
