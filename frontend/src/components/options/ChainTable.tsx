import { useEffect, useRef } from "react";

import type { ChainResponse, LegQuote, OptionKind, StrikeRow } from "../../types/options";
import { formatStrike } from "../../utils/occ";

export type LegRole = "long" | "short";

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

function Side({
  quote,
  kind,
  itm,
  role,
  pickable,
  onPick,
}: {
  quote: LegQuote | null;
  kind: OptionKind;
  itm: boolean;
  role: LegRole | undefined;
  pickable: boolean;
  onPick: () => void;
}) {
  const cls = [
    "chain-side",
    kind,
    itm ? "chain-itm" : "",
    role ? `chain-leg-${role}` : "",
    pickable && quote ? "chain-pickable" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const cells =
    kind === "call"
      ? [oi(quote?.open_interest ?? 0), pct(quote?.iv ?? null), num(quote?.delta ?? null, 2), num(quote?.bid ?? null, 2), num(quote?.mid ?? null, 2), num(quote?.ask ?? null, 2)]
      : [num(quote?.bid ?? null, 2), num(quote?.mid ?? null, 2), num(quote?.ask ?? null, 2), num(quote?.delta ?? null, 2), pct(quote?.iv ?? null), oi(quote?.open_interest ?? 0)];
  return (
    <>
      {cells.map((cell, i) => (
        <td
          key={i}
          className={cls}
          onClick={pickable && quote ? onPick : undefined}
          title={quote ? `${quote.symbol}${quote.tradable ? "" : " (not tradable)"}` : "no contract"}
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
            <th>OI</th>
            <th>IV</th>
            <th>Δ</th>
            <th>Bid</th>
            <th>Mid</th>
            <th>Ask</th>
            <th className="chain-strike" />
            <th>Bid</th>
            <th>Mid</th>
            <th>Ask</th>
            <th>Δ</th>
            <th>IV</th>
            <th>OI</th>
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
                  onPick={() => onPick("call", row.strike)}
                />
                <td className="chain-strike">{formatStrike(row.strike)}</td>
                <Side
                  quote={row.put}
                  kind="put"
                  itm={row.strike > chain.spot}
                  role={selection.get(legKey("put", row.strike))}
                  pickable={pickable !== "call"}
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
