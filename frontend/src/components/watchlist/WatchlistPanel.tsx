import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { searchSymbols } from "../../api/http";
import { useWatchlist } from "../../hooks/useWatchlist";
import { useWatchlistQuotes } from "../../hooks/useWatchlistQuotes";
import type { SymbolSuggestion, WatchlistQuote } from "../../types/watchlist";
import { readDroppedSymbol, symbolDragProps, TICKER_RE } from "../../utils/dragSymbol";
import { formatPct, formatPrice, formatVolume } from "../../utils/format";

// Same debounce this codebase already uses for OrderTicket's preview call --
// short enough to feel live, long enough not to fire a search on every
// keystroke of a fast typist.
const SUGGEST_DEBOUNCE_MS = 350;

type SortColumn = "symbol" | "last" | "pctChange" | "volume";
type SortDirection = "asc" | "desc";

interface Row {
  symbol: string;
  quote: WatchlistQuote | undefined;
}

const SORT_KEYS: Record<SortColumn, (r: Row) => number | string | null> = {
  symbol: (r) => r.symbol,
  last: (r) => r.quote?.last ?? null,
  pctChange: (r) => r.quote?.pctChange ?? null,
  volume: (r) => r.quote?.volume ?? null,
};

function sortRows(rows: Row[], column: SortColumn, direction: SortDirection): Row[] {
  const keyFn = SORT_KEYS[column];
  const withValue = rows.filter((r) => keyFn(r) !== null);
  const withoutValue = rows.filter((r) => keyFn(r) === null);
  withValue.sort((a, b) => {
    const av = keyFn(a) as number | string;
    const bv = keyFn(b) as number | string;
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return direction === "asc" ? cmp : -cmp;
  });
  // A symbol with no quote yet isn't "lowest" -- same convention as
  // ScannerBenchmarkWidget's sortPicks.
  return [...withValue, ...withoutValue];
}

function pctClass(pct: number | null | undefined): string {
  if (pct == null || pct === 0) return "";
  return pct > 0 ? "delta-up" : "delta-down";
}

interface WatchlistPanelProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

export function WatchlistPanel({ selectedSymbol, onSelectSymbol }: WatchlistPanelProps) {
  const { symbols, addSymbol, removeSymbol } = useWatchlist();
  const { quotes, error } = useWatchlistQuotes(symbols);

  const [input, setInput] = useState("");
  const [suggestions, setSuggestions] = useState<SymbolSuggestion[]>([]);
  // -1 = nothing highlighted (Enter falls through to the form's plain
  // commit(input) instead of picking a row). Arrow keys move this; mouse
  // hover syncs it too, so keyboard and pointer never disagree about which
  // row is highlighted.
  const [activeIndex, setActiveIndex] = useState(-1);
  const [sortColumn, setSortColumn] = useState<SortColumn | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const suggestTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [dragActive, setDragActive] = useState(false);
  // dragenter/dragleave both bubble from every child element the pointer
  // crosses while dragging over the panel -- a plain boolean would flicker
  // off on each child boundary. Counting enters vs. leaves and only clearing
  // at zero is the standard fix.
  const dragDepth = useRef(0);

  const inputRef = useRef<HTMLInputElement | null>(null);
  // The dropdown itself is rendered through a portal (see below for why) and
  // so needs its own ref for the outside-click check -- it's no longer a DOM
  // descendant of inputRef.
  const suggestionsMenuRef = useRef<HTMLUListElement | null>(null);
  const [suggestionsPos, setSuggestionsPos] = useState<{ top: number; left: number; width: number } | null>(
    null,
  );

  useEffect(() => {
    if (suggestTimer.current) clearTimeout(suggestTimer.current);
    const q = input.trim();
    if (q.length === 0) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    suggestTimer.current = setTimeout(() => {
      searchSymbols(q)
        .then((res) => {
          if (!cancelled) setSuggestions(res.matches.filter((s) => !symbols.includes(s.symbol)));
        })
        .catch(() => {
          if (!cancelled) setSuggestions([]);
        });
    }, SUGGEST_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      if (suggestTimer.current) clearTimeout(suggestTimer.current);
    };
  }, [input, symbols]);

  useEffect(() => {
    setActiveIndex(-1);
  }, [suggestions]);

  // Measures where to portal the dropdown, and closes it on an outside
  // click or Escape -- same pattern ChartWidget's Levels menu uses, for the
  // same reason: .widget clips overflow, so an absolutely-positioned
  // dropdown here would render but get cut off at the widget's own edge.
  useEffect(() => {
    if (suggestions.length === 0) return;
    const input = inputRef.current;
    if (input) {
      const rect = input.getBoundingClientRect();
      setSuggestionsPos({ top: rect.bottom + 4, left: rect.left, width: rect.width });
    }
    function handlePointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (inputRef.current?.contains(target)) return;
      if (suggestionsMenuRef.current?.contains(target)) return;
      setSuggestions([]);
    }
    function handleKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") setSuggestions([]);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [suggestions.length]);

  function commit(raw: string) {
    const upper = raw.trim().toUpperCase();
    if (!TICKER_RE.test(upper)) return;
    addSymbol(upper);
    setInput("");
    setSuggestions([]);
  }

  // Arrow keys move the highlighted suggestion; Enter with one highlighted
  // picks it (the form's own onSubmit only ever sees the raw typed text, so
  // this has to intercept Enter itself rather than letting submit handle
  // it). Escape closing is handled by the same document-level listener
  // that closes on an outside click, above.
  function handleInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
    } else if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      commit(suggestions[activeIndex].symbol);
    }
  }

  function handleDragEnter(e: DragEvent) {
    e.preventDefault();
    dragDepth.current += 1;
    setDragActive(true);
  }

  function handleDragLeave(e: DragEvent) {
    e.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragActive(false);
  }

  function handleDragOver(e: DragEvent) {
    // Required for onDrop to fire at all -- a dragover the browser doesn't
    // see preventDefault() on is treated as "not a valid drop target".
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    dragDepth.current = 0;
    setDragActive(false);
    const symbol = readDroppedSymbol(e);
    if (symbol) addSymbol(symbol);
  }

  function handleSort(column: SortColumn) {
    if (column === sortColumn) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("desc");
    }
  }

  const rows = useMemo<Row[]>(() => symbols.map((symbol) => ({ symbol, quote: quotes[symbol] })), [symbols, quotes]);
  const sortedRows = useMemo(
    () => (sortColumn ? sortRows(rows, sortColumn, sortDirection) : rows),
    [rows, sortColumn, sortDirection],
  );

  const headerProps = (column: SortColumn) => ({
    className: "sortable-header",
    "aria-sort": (sortColumn === column ? (sortDirection === "asc" ? "ascending" : "descending") : "none") as
      | "ascending"
      | "descending"
      | "none",
    onClick: () => handleSort(column),
  });

  const sortIndicator = (column: SortColumn) =>
    sortColumn === column ? (sortDirection === "asc" ? " ▲" : " ▼") : "";

  return (
    <div
      className={`widget watchlist-widget${dragActive ? " watchlist-drop-active" : ""}`}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <div className="widget-header">
        <h2>Watchlist</h2>
        <form
          className="watchlist-add-form"
          onSubmit={(e) => {
            e.preventDefault();
            commit(input);
          }}
        >
          <input
            ref={inputRef}
            className="watchlist-add-input"
            type="text"
            value={input}
            placeholder="Add symbol…"
            autoComplete="off"
            role="combobox"
            aria-expanded={suggestions.length > 0}
            aria-autocomplete="list"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleInputKeyDown}
          />
          <button type="submit" className="tab" disabled={!TICKER_RE.test(input.trim().toUpperCase())}>
            Add
          </button>
        </form>
        <span className="widget-count">{symbols.length}</span>
      </div>

      {suggestions.length > 0 &&
        suggestionsPos &&
        createPortal(
          // Portalled to <body> rather than positioned relative to the
          // input: .widget clips overflow, so an absolutely-positioned
          // dropdown here would render but get cut off at the widget's own
          // edge -- see ChartWidget's identical Levels-menu comment.
          <ul
            className="watchlist-suggestions-menu"
            role="listbox"
            ref={suggestionsMenuRef}
            style={{ top: suggestionsPos.top, left: suggestionsPos.left, minWidth: suggestionsPos.width }}
          >
            {suggestions.map((s, i) => (
              <li
                key={s.symbol}
                role="option"
                aria-selected={i === activeIndex}
                className={i === activeIndex ? "watchlist-suggestion-item active" : "watchlist-suggestion-item"}
                onMouseEnter={() => setActiveIndex(i)}
                // mousedown, not click: fires before the input would lose
                // focus, so preventDefault here keeps focus in the input
                // instead of wherever a stray blur would send it.
                onMouseDown={(e) => {
                  e.preventDefault();
                  commit(s.symbol);
                }}
              >
                <span className="watchlist-suggestion-symbol">{s.symbol}</span>
                <span className="watchlist-suggestion-name">{s.name ?? "—"}</span>
                <span className="exchange-tag">{s.exchange}</span>
              </li>
            ))}
          </ul>,
          document.body,
        )}

      {error && <p className="widget-error">{error}</p>}

      <div className="widget-body">
        {symbols.length === 0 ? (
          <div className="widget-empty">
            No symbols yet -- add one above, or drag one in from a scanner.
          </div>
        ) : (
          <table className="scanner-table">
            <thead>
              <tr>
                <th {...headerProps("symbol")}>Symbol{sortIndicator("symbol")}</th>
                <th {...headerProps("last")}>Last{sortIndicator("last")}</th>
                <th {...headerProps("pctChange")}>% Chg{sortIndicator("pctChange")}</th>
                <th {...headerProps("volume")}>Volume{sortIndicator("volume")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sortedRows.map(({ symbol, quote }) => (
                <tr
                  key={symbol}
                  aria-selected={symbol === selectedSymbol}
                  onClick={() => onSelectSymbol(symbol)}
                >
                  <td className="symbol-cell" {...symbolDragProps(symbol)}>
                    {symbol}
                  </td>
                  <td>{quote ? formatPrice(quote.last) : "—"}</td>
                  <td className={pctClass(quote?.pctChange)}>
                    {quote?.pctChange != null ? formatPct(quote.pctChange) : "—"}
                  </td>
                  <td>{quote?.volume != null ? formatVolume(quote.volume) : "—"}</td>
                  <td>
                    <button
                      type="button"
                      className="watchlist-remove-button"
                      title={`Remove ${symbol} from the watchlist`}
                      onClick={(e) => {
                        e.stopPropagation();
                        removeSymbol(symbol);
                      }}
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
