import { useEffect, useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";

import type { ChainResponse, LegQuote, OptionKind, StrikeRow } from "../../types/options";
import { formatStrike } from "../../utils/occ";
import { formatClock, timeZoneLabel } from "../../utils/time";
import { symbolDragProps } from "../../utils/dragSymbol";
import { updateSettings, type ChainGreek } from "../../api/settings";
import { useSettings } from "../../hooks/useSettings";
import { atmIv } from "../../utils/atmIv";

/** The greek column cycles Δ -> Γ -> Θ -> V on a header click; the choice is
 * kept in the settings so every chain shows the same one. One column
 * rather than three: the table is already thirteen columns beside a
 * ticket, and the cell tooltip carries all four regardless. */
const GREEKS: { key: ChainGreek; label: string; title: string; digits: number }[] = [
  { key: "delta", label: "Δ", title: "Delta: option move per 1 $ of the underlying, and roughly the odds of expiring in the money. Click for gamma.", digits: 2 },
  { key: "gamma", label: "Γ", title: "Gamma: how much delta changes per 1 $ of the underlying -- how fast a position turns. Click for theta.", digits: 3 },
  { key: "theta", label: "Θ", title: "Theta: value lost per day at a standing price, per share -- negative for a bought option. Click for vega.", digits: 2 },
  { key: "vega", label: "V", title: "Vega: value gained per share when implied volatility rises one point -- largest at the money and far out in time. Click for delta.", digits: 3 },
];

function greekValue(quote: LegQuote | null, greek: ChainGreek): number | null {
  if (!quote) return null;
  if (greek === "delta") return quote.delta;
  if (greek === "gamma") return quote.gamma ?? null;
  if (greek === "theta") return quote.theta ?? null;
  return quote.vega ?? null;
}

function greeksNote(quote: LegQuote): string {
  return ` -- Δ ${num(quote.delta, 2)} · Γ ${num(quote.gamma ?? null, 3)} · Θ ${num(quote.theta ?? null, 2)} · V ${num(quote.vega ?? null, 3)}`;
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

// --- the readable layout -------------------------------------------------------

/** What crossing a quote costs against its mid, as the Screener reads it:
 * up to 10 % passes its quote-width criterion, past 25 % a mid limit is
 * mostly a wish. Null without a two-sided quote. */
export function spreadFraction(quote: LegQuote | null): number | null {
  if (!quote || quote.bid == null || quote.ask == null || quote.bid <= 0 || quote.ask < quote.bid) return null;
  const mid = (quote.bid + quote.ask) / 2;
  return mid > 0 ? (quote.ask - quote.bid) / mid : null;
}
const SPREAD_TIGHT = 0.1;
const SPREAD_WIDE = 0.25;

export function spreadGrade(fraction: number | null): "tight" | "fair" | "wide" | "none" {
  if (fraction == null) return "none";
  if (fraction <= SPREAD_TIGHT) return "tight";
  if (fraction <= SPREAD_WIDE) return "fair";
  return "wide";
}

/** Fewer characters for the same number: IV without its percent sign (the
 * header says it), delta without its leading zero, open interest in k from
 * a thousand. */
function readableIv(value: number | null): string {
  return value == null ? "—" : (value * 100).toFixed(0);
}

function readableGreek(value: number | null, digits: number, key: ChainGreek): string {
  if (value == null) return "—";
  const text = value.toFixed(digits);
  return key === "delta" ? text.replace(/^(-?)0\./, "$1.") : text;
}

function readableCount(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k` : String(value);
}

/** The strike interval the eye should be ruled by: the chain's usual step
 * times about five, rounded to a number people count in. */
export function roundStep(strikes: number[]): number | null {
  const diffs: number[] = [];
  for (let i = 1; i < strikes.length; i++) {
    const d = Math.round((strikes[i] - strikes[i - 1]) * 100) / 100;
    if (d > 0) diffs.push(d);
  }
  if (diffs.length === 0) return null;
  const counts = new Map<number, number>();
  for (const d of diffs) counts.set(d, (counts.get(d) ?? 0) + 1);
  const step = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0][0];
  return [1, 2.5, 5, 10, 25, 50, 100, 250, 500].find((n) => n >= step * 4) ?? null;
}

/** "-2.1%" from spot, and the same distance in standard deviations to
 * expiry when the chain carries an at-the-money IV. */
function distance(strike: number, spot: number, sigmaToExpiry: number | null): { pct: string; sigma: string | null } {
  const rel = strike / spot - 1;
  const pctText = `${rel > 0 ? "+" : rel < 0 ? "−" : ""}${Math.abs(rel * 100).toFixed(1)}%`;
  const sigma = sigmaToExpiry && sigmaToExpiry > 0 ? Math.log(strike / spot) / sigmaToExpiry : null;
  return { pct: pctText, sigma: sigma == null ? null : `${sigma > 0 ? "+" : ""}${sigma.toFixed(2)}σ` };
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
  oiUnreported,
  asOfMs,
  greek,
  readable,
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
  /** This expiry reported no open interest on any strike. Alpaca leaves
   * the field unset for a whole series sometimes, and a column of zeros
   * then says "nothing is open here" about a chain nobody reported on. */
  oiUnreported: boolean;
  asOfMs: number;
  greek: { key: ChainGreek; digits: number };
  /** The weighted layout (Settings → Display → Chain layout). */
  readable: boolean;
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
  const count = readable ? readableCount : oi;
  const oiCell = replay || oiUnreported ? "—" : count(quote?.open_interest ?? 0);
  // Volume is the session's own trades: a strike with open interest but no
  // volume is an old position nobody is touching today. Unknown (a replayed
  // day, or a chain built without day bars) reads as a dash, not as zero.
  const volCell = replay || quote?.volume == null ? "—" : count(quote.volume);
  const greekValueNow = greekValue(quote, greek.key);
  const greekCell = readable ? readableGreek(greekValueNow, greek.digits, greek.key) : num(greekValueNow, greek.digits);
  const ivCell = readable ? readableIv(quote?.iv ?? null) : pct(quote?.iv ?? null);
  // The mid carries a dot for how wide its quote is -- not in a replay,
  // where bid and ask are the last print plus a fixed slippage.
  const fraction = readable && !replay ? spreadFraction(quote) : null;
  const grade = readable && !replay && quote ? spreadGrade(fraction) : null;
  const midCell: ReactNode = (
    <>
      {num(quote?.mid ?? null, 2)}
      {grade && (
        <span
          className={`spread-dot spread-${grade}`}
          aria-label={grade === "none" ? "no two-sided quote" : `${grade} spread`}
        />
      )}
    </>
  );
  type Cell = { text: ReactNode; col: "meta" | "iv" | "greek" | "quote" | "mid"; title?: string };
  const midTitle =
    grade == null
      ? undefined
      : grade === "none"
        ? "No two-sided quote: the mid is a last print or nothing"
        : `Bid/ask ${((fraction ?? 0) * 100).toFixed(0)} % of the mid -- ${grade === "tight" ? "tight (up to 10 %, the Screener's bar)" : grade === "fair" ? "fair (10-25 %): a mid limit may need patience" : "wide (over 25 %): a mid limit is mostly a wish"}`;
  const left: Cell[] = [
    { text: oiCell, col: "meta" },
    { text: volCell, col: "meta" },
    { text: ivCell, col: "iv" },
    { text: greekCell, col: "greek" },
    { text: num(quote?.bid ?? null, 2), col: "quote" },
    { text: midCell, col: "mid", title: midTitle },
    { text: num(quote?.ask ?? null, 2), col: "quote" },
  ];
  const cells: Cell[] = kind === "call" ? left : [left[4], left[5], left[6], left[3], left[2], left[1], left[0]];
  const printNote = lastAt != null ? ` -- last print ${lastPrintLabel(lastAt)} ${timeZoneLabel()}${stale ? " (stale)" : ""}` : replay && quote ? " -- no print yet today" : "";
  return (
    <>
      {cells.map((cell, i) => (
        <td
          key={i}
          className={`${cls} chain-col-${cell.col}${dropping ? " chain-drop-target" : ""}`}
          onClick={pickable && quote ? onPick : undefined}
          title={
            quote
              ? `${cell.title ? `${cell.title}\n` : ""}${quote.symbol}${quote.tradable ? "" : " (not tradable)"}${role === "body" ? " -- body, sold x2" : ""}${greeksNote(quote)}${printNote}${
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
          {cell.text}
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
  // Zeros interleaved with real values are a real "nothing open on this
  // strike" (see gamma_exposure's own note); zeros everywhere are the feed
  // not reporting, and the same distinction the backend draws in
  // options_resolve.reports_open_interest.
  const oiUnreported = chain.rows.every(
    (row) => !row.call?.open_interest && !row.put?.open_interest,
  );
  const asOfMs = Date.parse(chain.as_of);
  const [settings] = useSettings();
  const readable = settings.chainLayout === "readable";
  const greek = GREEKS.find((g) => g.key === settings.chainGreek) ?? GREEKS[0];
  const ruled = readable ? roundStep(chain.rows.map((r) => r.strike)) : null;
  // One standard deviation to expiry, for the strike's distance in sigmas.
  const ivNow = readable ? atmIv(chain) : null;
  const yearsLeft = Math.max(0, (Date.parse(`${chain.expiry}T20:00:00Z`) - Date.parse(chain.as_of)) / (365 * 24 * 3600 * 1000));
  const sigmaToExpiry = ivNow && yearsLeft > 0 ? ivNow * Math.sqrt(yearsLeft) : null;
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
  const oiTitle = replay ? "Open interest is not known for a replayed day" : "Open positions outstanding in this contract";
  const volTitle = replay
    ? "Volume is not known for a replayed day"
    : "Contracts traded in this session. Open interest without volume is a position nobody is touching today.";
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
      <table className={`performance-table chain-table${readable ? " chain-readable" : ""}`}>
        <thead>
          <tr>
            <th colSpan={7} className="chain-group">
              Calls
            </th>
            <th className="chain-strike">Strike</th>
            <th colSpan={7} className="chain-group">
              Puts
            </th>
          </tr>
          <tr>
            <th title={oiTitle}>OI</th>
            <th title={volTitle}>Vol</th>
            <th title={replay ? "Implied volatility solved from the last print" : undefined}>IV{readable ? " %" : ""}</th>
            {greekHeader}
            <th title={quoteTitle}>Bid{replay ? "*" : ""}</th>
            <th>{replay ? "Last" : "Mid"}</th>
            <th title={quoteTitle}>Ask{replay ? "*" : ""}</th>
            <th className="chain-strike" />
            <th title={quoteTitle}>Bid{replay ? "*" : ""}</th>
            <th>{replay ? "Last" : "Mid"}</th>
            <th title={quoteTitle}>Ask{replay ? "*" : ""}</th>
            {greekHeader}
            <th title={replay ? "Implied volatility solved from the last print" : undefined}>IV{readable ? " %" : ""}</th>
            <th title={volTitle}>Vol</th>
            <th title={oiTitle}>OI</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) =>
            row === "spot" ? (
              <tr key="spot" ref={spotRowRef} className="chain-spot-row">
                <td colSpan={15}>spot {chain.spot.toFixed(2)}</td>
              </tr>
            ) : (
              <tr
                key={row.strike}
                className={ruled && Math.abs(row.strike / ruled - Math.round(row.strike / ruled)) < 1e-6 ? "chain-row-round" : undefined}
              >
                <Side
                  quote={row.call}
                  kind="call"
                  strike={row.strike}
                  onMoveLeg={onMoveLeg}
                  itm={row.strike < chain.spot}
                  role={selection.get(legKey("call", row.strike))}
                  pickable={pickable !== "put"}
                  replay={replay}
                  oiUnreported={oiUnreported}
                  asOfMs={asOfMs}
                  greek={greek}
                  readable={readable}
                  onPick={() => onPick("call", row.strike)}
                />
                {readable ? (
                  (() => {
                    const d = distance(row.strike, chain.spot, sigmaToExpiry);
                    return (
                      <td
                        className="chain-strike"
                        title={`${formatStrike(row.strike)}: ${d.pct} from the price${d.sigma ? `, ${d.sigma} to expiry at the at-the-money IV` : ""}`}
                      >
                        {formatStrike(row.strike)}
                        <span className="chain-dist">{d.pct}</span>
                      </td>
                    );
                  })()
                ) : (
                  <td className="chain-strike">{formatStrike(row.strike)}</td>
                )}
                <Side
                  quote={row.put}
                  kind="put"
                  strike={row.strike}
                  onMoveLeg={onMoveLeg}
                  itm={row.strike > chain.spot}
                  role={selection.get(legKey("put", row.strike))}
                  pickable={pickable !== "call"}
                  replay={replay}
                  oiUnreported={oiUnreported}
                  asOfMs={asOfMs}
                  greek={greek}
                  readable={readable}
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
