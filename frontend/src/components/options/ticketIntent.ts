/**
 * A request from elsewhere on the dashboard -- the Earnings screen's
 * "Load into ticket" on a priced structure -- for the Options widget to
 * plant a structure in its spread ticket.
 *
 * The same module-level bus as optimizerIntent and playbookIntent, for
 * the same reason: the widgets are siblings under the dock and share no
 * parent that should know about option structures. Inside the Options
 * widget "Load into ticket" is a prop callback; from outside it it has to
 * be this.
 *
 * The symbol travels with the structure because the widget may still be
 * on another one when the click happens: the caller selects the symbol
 * first, and the widget holds the structure until its own `symbol` prop
 * catches up.
 */

import type { LoadableStructure } from "../../types/options";

export interface TicketIntent {
  symbol: string;
  structure: LoadableStructure;
  seq: number;
}

type Listener = (intent: TicketIntent) => void;

const listeners = new Set<Listener>();
let seq = 0;

export function requestTicket(intent: Omit<TicketIntent, "seq">): void {
  const full = { ...intent, seq: ++seq };
  for (const l of listeners) l(full);
}

export function subscribeTicketIntent(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
