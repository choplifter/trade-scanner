/**
 * A request from elsewhere on the dashboard -- the Positions tab's "Wheel…"
 * on a share position -- for the Options widget to open its Playbooks tab
 * with the start form prefilled for a symbol. Same module-level bus as
 * optimizerIntent: the widgets are siblings under the dock and share no
 * parent that should know about option campaigns. Delivered to widgets
 * mounted at the time of the click only.
 */

export interface PlaybookIntent {
  symbol: string;
  playbook: string;
  seq: number;
}

type Listener = (intent: PlaybookIntent) => void;

const listeners = new Set<Listener>();
let seq = 0;

export function requestPlaybook(intent: Omit<PlaybookIntent, "seq">): void {
  const full = { ...intent, seq: ++seq };
  for (const l of listeners) l(full);
}

export function subscribePlaybookIntent(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
