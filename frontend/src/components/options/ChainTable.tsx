import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";

import type { ChainResponse, LegQuote, OptionKind, StrikeRow } from "../../types/options";
import { formatStrike } from "../../utils/occ";
import { formatClock, timeZoneLabel } from "../../utils/time";
import { symbolDragProps } from "../../utils/dragSymbol";
import { updateSettings, type ChainGreek } from "../../api/settings";
import { useSettings } from "../../hooks/useSettings";

/** The greek column cycles Δ -> Γ -> Θ on a header click; the choice is
 * kept in the settings so every chain shows the same one. One column
 * rather than three: the table is already thirteen columns beside a
 * ticket, and the cell tooltip carries all three regardless. */
const GREEKS: { key: ChainGreek; label: string; title: string; digits: number }[] = [
  { key: "delta", label: "Δ", title: "Delta: option move per 1 $ of the underlying, and roughly the odds of expiring in the money. Click for gamma.", digits: 2 },
  { key: "gamma", label: "Γ", title: "Gamma: how much delta changes per 1 $ of the underlying -- how fast a position turns. Click for theta.", digits: 3 },
  { key: "theta", label: "Θ", title: "Theta: value lost per day at a standing price, per share -- negative for a bought option. Click for delta.", digits: 2 },
];

function greekValue(quote: LegQuote | null, greek: ChainGreek): number | null {
  if (!quote) return null;
  return greek === "delta" ? quote.delta : greek === "gamma" ? (quote.gamma ?? null) : (quote.theta ?? null);
}

function greeksNote(quote: LegQuote): string {
  return ` -- Δ ${num(quote.delta, 2)} · Γ ${num(quote.gamma ?? null, 3)} · Θ ${num(quote.theta ?? null, 2)}`;
}

/** `body` is a butterfly's doubled short. */
export type LegRole = "long" | "short" | "body";

/** `${kind}:${strike}` -> role, for the legs currently selected. */
export type LegSelection = Map<string, LegRole>;

/** Moving a leg from one contract to another inside the table.
 *
 * Every cell is already a drag source for its own contract symbol --
 * dropping it on a chart opens that contract's premium chart -- and one
 * gesture on one cell cannot mean two things. So the leg drag is offered
 * only where it is unambiguous: on the cells that are part of the package
 * being built. Those carry the leg; every other cell is unchanged. */
const LEG_MIME = "application/x-option-leg";

export interface LegRef {
  kind: OptionKind;
  strike: number;
}

function readDraggedLeg(e: DragEvent<HTMLTableCellElement>): LegRef | null {
  const raw = e.dataTransfer.getData(LEG_MIME);
  const [kind, strike] = raw.split(":");
  const price = Number(strike);
  if ((kind !== "call" && kind !== "put") || !Number.isFinite(price)) return null;
  return { kind, strike: price };
}

export function legKey(kind: OptionKind, strike: number): string {
  return `${kind}:${strike}`;
}

interface ChainTableProps {
  chain: ChainResponse;
  selection: LegSelection;
  /** Given, a cell holding a leg drags it to the contract it is dropped
   * on (the builder). Without it the table behaves as it always has. */
  onMoveLeg?: (from: LegRef, to: LegRef) => void;
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
  return formatClock(lastAt);
}

function Side({
  quote,
  kind,
  strike,
  itm,
  role,
  pickable,
  replay,
  asOfMs,
  greek,
  onPick,
  onMoveLeg,
}: {
  quote: LegQuote | null;
  kind: OptionKind;
  strike: number;
  itm: boolean;
  role: LegRole | undefined;
  pickable: boolean;
  /** A replayed chain: no open interest, synthetic bid/ask, stale prints. */
  replay: boolean;
  asOfMs: number;
  greek: { key: ChainGreek; digits: number };
  onPick: () => void;
  onMoveLeg?: (from: LegRef, to: LegRef) => void;
}) {
  const [dropping, setDropping] = useState(false);
  // A cell drags its leg only when it holds one and the builder is
  // showing; otherwise it keeps dragging its symbol.
  const dragsLeg = Boolean(onMoveLeg && role && quote);
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
  const greekCell = num(greekValue(quote, greek.key), greek.digits);
  const cells =
    kind === "call"
      ? [oiCell, pct(quote?.iv ?? null), greekCell, num(quote?.bid ?? null, 2), num(quote?.mid ?? null, 2), num(quote?.ask ?? null, 2)]
      : [num(quote?.bid ?? null, 2), num(quote?.mid ?? null, 2), num(quote?.ask ?? null, 2), greekCell, pct(quote?.iv ?? null), oiCell];
  const printNote = lastAt != null ? ` -- last print ${lastPrintLabel(lastAt)} ${timeZoneLabel()}${stale ? " (stale)" : ""}` : replay && quote ? " -- no print yet today" : "";
  return (
    <>
      {cells.map((cell, i) => (
        <td
          key={i}
          className={`${cls}${dropping ? " chain-drop-target" : ""}`}
          onClick={pickable && quote ? onPick : undefined}
          title={
            quote
              ? `${quote.symbol}${quote.tradable ? "" : " (not tradable)"}${role === "body" ? " -- body, sold x2" : ""}${greeksNote(quote)}${printNote}${
                  dragsLeg ? " -- drag onto another strike to move this leg" : " -- drag onto a chart for its premium chart"
                }`
              : "no contract"
          }
          {...(quote && !dragsLeg ? symbolDragProps(quote.symbol) : {})}
          {...(dragsLeg
            ? {
                draggable: true,
                onDragStart: (e: DragEvent<HTMLTableCellElement>) => {
                  e.dataTransfer.setData(LEG_MIME, `${kind}:${strike}`);
                  e.dataTransfer.effectAllowed = "move";
                },
              }
            : {})}
          {...(onMoveLeg && quote
            ? {
                onDragOver: (e: DragEvent<HTMLTableCellElement>) => {
                  if (!e.dataTransfer.types.includes(LEG_MIME)) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  if (!dropping) setDropping(true);
                },
                onDragLeave: () => setDropping(false),
                onDrop: (e: DragEvent<HTMLTableCellElement>) => {
                  e.preventDefault();
                  setDropping(false);
                  const from = readDraggedLeg(e);
                  if (!from || (from.kind === kind && from.strike === strike)) return;
                  onMoveLeg(from, { kind, strike });
                },
              }
            : {})}
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
export function ChainTable({ chain, selection, pickable, onPick, onMoveLeg }: ChainTableProps) {
  const replay = chain.feed === "replay";
  const asOfMs = Date.parse(chain.as_of);
  const [settings] = useSettings();
  const greek = GREEKS.find((g) => g.key === settings.chainGreek) ?? GREEKS[0];
  const cycleGreek = () => {
    const next = GREEKS[(GREEKS.indexOf(greek) + 1) % GREEKS.length];
    updateSettings({ chainGreek: next.key });
  };
  const greekHeader = (
    <th>
      <button type="button" className="chain-greek-toggle" onClick={cycleGreek} title={greek.title} aria-label={`Greek column: ${greek.key}`}>
        {greek.label}
      </button>
    </th>
  );
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
            {greekHeader}
            <th title={quoteTitle}>Bid{replay ? "*" : ""}</th>
            <th>{replay ? "Last" : "Mid"}</th>
            <th title={quoteTitle}>Ask{replay ? "*" : ""}</th>
            <th className="chain-strike" />
            <th title={quoteTitle}>Bid{replay ? "*" : ""}</th>
            <th>{replay ? "Last" : "Mid"}</th>
            <th title={quoteTitle}>Ask{replay ? "*" : ""}</th>
            {greekHeader}
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
                  strike={row.strike}
                  onMoveLeg={onMoveLeg}
                  itm={row.strike < chain.spot}
                  role={selection.get(legKey("call", row.strike))}
                  pickable={pickable !== "put"}
                  replay={replay}
                  asOfMs={asOfMs}
                greek={greek}
                  onPick={() => onPick("call", row.strike)}
                />
                <td className="chain-strike">{formatStrike(row.strike)}</td>
                <Side
                  quote={row.put}
                  kind="put"
                  strike={row.strike}
                  onMoveLeg={onMoveLeg}
                  itm={row.strike > chain.spot}
                  role={selection.get(legKey("put", row.strike))}
                  pickable={pickable !== "call"}
                  replay={replay}
                  asOfMs={asOfMs}
                greek={greek}
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
