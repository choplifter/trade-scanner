/** A per-user note on one closed trade -- see backend
 * app.trading.journal_store.JournalEntry. trade_id matches a real Trade.id
 * or a Simulation Mode trade's id (whichever the current trading mode's
 * trade list uses), never both at once. */
export interface JournalEntry {
  trade_id: string;
  note: string;
  rating: number | null;
  tags: string[];
  updated_at: string;
}

/** GET /api/trading/journal -- keyed by trade_id; a trade with no entry is
 * simply absent from the map. */
export interface JournalResponse {
  entries: Record<string, JournalEntry>;
}
