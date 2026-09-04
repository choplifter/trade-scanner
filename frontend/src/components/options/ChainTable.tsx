import { useEffect, useRef } from "react";

import type { ChainResponse, LegQuote, OptionKind, StrikeRow } from "../../types/options";
import { formatStrike } from "../../utils/occ";
import { symbolDragProps } from "../../utils/dragSymbol";

/** `body` is a butterfly's doubled short. */
export type LegRole = "long" | "short" | "body";

/** `${kind}:${strike}` -> role, for the legs currently selected. */
export type LegSelection = Map<string, LegRole>;

export function legKey(kind: OptionKind, strike: number): string {
  return `${kind}:${strike}`;
}

interface ChainTableProps {
  chain: ChainResponse;
  selection: LegSelection;
  /** The kind the current strategy trades; cells of the other kind are
   * shown but not pickable (an iron condor picks both). */
  pickable: OptionKind | "both";
  onPick: (kind: OptionKind, strike: number) => void;
}

function num(value: number | null, digits: number): string {
  return value == null ? "—" : value.toFixed(digits);
}

function pct(value: number | null): string {
  return value == null ? "—" : `${(value * 100).toFixed(0)}%`;
}

function oi(value: number): string {
  return value >= 10_000 ? `${(value / 1000).toFixed(1)}k` : String(value);
}

/** A replayed print older than this at the replay clock is shown faded. */
const STALE_MS = 30 * 60 * 1000;

function lastPrintLabel(lastAt: string): string {
  return new Date(lastAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" });
}

function Side({
  quote,
  kind,
  itm,
  role,
  pickable,
  replay,
  asOfMs,
  onPick,
}: {
  quote: LegQuote | null;
  kind: OptionKind;
  itm: boolean;
  role: LegRole | undefined;
  pickable: boolean;
  /** A replayed chain: no open interest, synthetic bid/ask, stale prints. */
  replay: boolean;
  asOfMs: number;
  onPick: () => void;
}) {
  const lastAt = replay ? (quote?.last_at ?? null) : null;
  const stale = lastAt != null && asOfMs - Date.parse(lastAt) > STALE_MS;
  const cls = [
    "chain-side",
    kind,
    itm ? "chain-itm" : "",
    role ? `chain-leg-${role === "body" ? "short chain-leg-body" : role}` : "",
    pickable && quote ? "chain-pickable" : "",
    stale ? "chain-stale" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const oiCell = replay ? "—" : oi(quote?.open_interest ?? 0);
  const cells =
    kind === "call"
      ? [oiCell, pct(quote?.iv ?? null), num(quote?.delta ?? null, 2), num(quote?.bid ?? null, 2), num(quote?.mid ?? null, 2), num(quote?.ask ?? null, 2)]
      : [num(quote?.bid ?? null, 2), num(quote?.mid ?? null, 2), num(quote?.ask ?? null, 2), num(quote?.delta ?? null, 2), pct(quote?.iv ?? null), oiCell];
  const printNote = lastAt != null ? ` -- last print ${lastPrintLabel(lastAt)} ET${stale ? " (stale)" : ""}` : replay && quote ? " -- no print yet today" : "";
  return (
    <>
      {cells.map((cell, i) => (
        <td
          key={i}
          className={cls}
          onClick={pickable && quote ? onPick : undefined}
          title={
            quote
              ? `${quote.symbol}${quote.tradable ? "" : " (not tradable)"}${role === "body" ? " -- body, sold x2" : ""}${printNote} -- drag onto a chart for its premium chart`
              : "no contract"
          }
          {...(quote ? symbolDragProps(quote.symbol) : {})}
        >
          {cell}
        </td>
      ))}
    </>
  );
}

/** The chain: calls on the left, strikes down the middle, puts on the
 * right, the way every broker lays it out. In-the-money cells are shaded;
 * a divider marks where spot sits; the selected legs are outlined by
 * role. Scrolls itself to spot when a new chain arrives. */
export function ChainTable({ chain, selection, pickable, onPick }: ChainTableProps) {
  const replay = chain.feed === "replay";
  const asOfMs = Date.parse(chain.as_of);
  const quoteTitle = replay ? "Replay: last print ± slippage (max(2%, 0.01)) -- the simulated fill price" : undefined;
  const oiTitle = replay ? "Open interest is not known for a replayed day" : undefined;
  const spotRowRef = useRef<HTMLTableRowElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrolledFor = useRef<string | null>(null);

  const key = `${chain.underlying}:${chain.expiry}`;
  useEffect(() => {
    if (scrolledFor.current === key) return;
    scrolledFor.current = key;
    const row = spotRowRef.current;
    const box = scrollRef.current;
    if (!row || !box) return;
    // Scroll only the chain box, not the page and the grid around it.
    const delta = row.getBoundingClientRect().top - box.getBoundingClientRect().top;
    box.scrollTop += delta - (box.clientHeight - row.offsetHeight) / 2;
  }, [key, chain.rows.length]);

  let dividerPlaced = false;
  const rows: (StrikeRow | "spot")[] = [];
  for (const row of chain.rows) {
    if (!dividerPlaced && row.strike > chain.spot) {
      rows.push("spot");
      dividerPlaced = true;
    }
    rows.push(row);
  }
  if (!dividerPlaced) rows.push("spot");

  return (
    <div className="chain-scroll" ref={scrollRef}>
      <table className="performance-table chain-table">
        <thead>
          <tr>
            <th colSpan={6} className="chain-group">
              Calls
            </th>
            <th className="chain-strike">Strike</th>
            <th colSpan={6} className="chain-group">
              Puts
            </th>
          </tr>
          <tr>
            <th title={oiTitle}>OI</th>
            <th title={replay ? "Implied volatility solved from the last print" : undefined}>IV</th>
            <th>Δ</th>
            <th title={quoteTitle}>Bid{replay ? "*" : ""}</th>
            <th>{replay ? "Last" : "Mid"}</th>
            <th title={quoteTitle}>Ask{replay ? "*" : ""}</th>
            <th className="chain-strike" />
            <th title={quoteTitle}>Bid{replay ? "*" : ""}</th>
            <th>{replay ? "Last" : "Mid"}</th>
            <th title={quoteTitle}>Ask{replay ? "*" : ""}</th>
            <th>Δ</th>
            <th title={replay ? "Implied volatility solved from the last print" : undefined}>IV</th>
            <th title={oiTitle}>OI</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) =>
            row === "spot" ? (
              <tr key="spot" ref={spotRowRef} className="chain-spot-row">
                <td colSpan={13}>spot {chain.spot.toFixed(2)}</td>
              </tr>
            ) : (
              <tr key={row.strike}>
                <Side
                  quote={row.call}
                  kind="call"
                  itm={row.strike < chain.spot}
                  role={selection.get(legKey("call", row.strike))}
                  pickable={pickable !== "put"}
                  replay={replay}
                  asOfMs={asOfMs}
                  onPick={() => onPick("call", row.strike)}
                />
                <td className="chain-strike">{formatStrike(row.strike)}</td>
                <Side
                  quote={row.put}
                  kind="put"
                  itm={row.strike > chain.spot}
                  role={selection.get(legKey("put", row.strike))}
                  pickable={pickable !== "call"}
                  replay={replay}
                  asOfMs={asOfMs}
                  onPick={() => onPick("put", row.strike)}
                />
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}
