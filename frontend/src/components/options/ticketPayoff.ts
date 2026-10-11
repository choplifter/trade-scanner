/**
 * The risk chart of the structure on the spread ticket, published for the
 * dock's own "Risk chart" panel -- so the chart can sit large beside the
 * chain instead of squeezed under the ticket.
 *
 * A module-level store rather than a context, like ticketIntent: the
 * ticket and the panel are siblings under the dock, and a payoff that
 * changes with every quote must not pass through App.tsx's widgets memo.
 */

import { useSyncExternalStore } from "react";

import type { Payoff } from "../../types/options";

export interface TicketPayoff {
  symbol: string;
  /** "Bull put · 17 Oct" -- what the panel's heading names. */
  title: string;
  payoff: Payoff;
  expiryLabel: string;
}

let current: TicketPayoff | null = null;
const listeners = new Set<() => void>();

export function publishTicketPayoff(next: TicketPayoff | null): void {
  if (next === current) return;
  current = next;
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useTicketPayoff(): TicketPayoff | null {
  return useSyncExternalStore(subscribe, () => current);
}
