import type { DragEvent } from "react";

// Custom MIME type so the watchlist's drop handler can tell "a symbol was
// dragged from inside this app" apart from an incidental text drag (e.g.
// dragging selected page text) landing on the same drop zone.
const SYMBOL_MIME = "application/x-stock-symbol";

// Loose on purpose: a dragged/typed symbol can be anything Alpaca trades,
// not just what the scanner's momentum universe ranks (which excludes ETFs
// and anything outside the price/volume band) -- see routers/watchlist.py.
// Allows a "." for share classes like BRK.B.
export const TICKER_RE = /^[A-Z]{1,5}(\.[A-Z])?$/;

/** Call from a draggable symbol row's onDragStart. Also sets a plain-text
 * fallback so dropping outside this app (a search bar, a notes app) still
 * pastes the ticker. */
export function startSymbolDrag(e: DragEvent, symbol: string): void {
  e.dataTransfer.setData(SYMBOL_MIME, symbol);
  e.dataTransfer.setData("text/plain", symbol);
  e.dataTransfer.effectAllowed = "copy";
}

/** Call from a drop zone's onDrop. Returns the dragged symbol, uppercased,
 * or null if nothing usable was dropped (wrong drag source, or text that
 * doesn't look like a ticker). */
export function readDroppedSymbol(e: DragEvent): string | null {
  const raw = e.dataTransfer.getData(SYMBOL_MIME) || e.dataTransfer.getData("text/plain");
  const upper = raw.trim().toUpperCase();
  return TICKER_RE.test(upper) ? upper : null;
}
