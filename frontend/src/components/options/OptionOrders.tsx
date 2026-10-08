import { useCallback, useEffect, useState } from "react";

import { cancelOptionOrder, getContractQuote, getOptionOrders } from "../../api/options";
import { liveConfirmed, type TradingMode } from "../../api/tradingMode";
import { useReplaySession } from "../../hooks/useReplaySession";
import type { LegQuote } from "../../types/options";
import type { Order } from "../../types/trading";
import { packageDragProps, symbolDragProps } from "../../utils/dragSymbol";
import { formatLeg, parseOcc } from "../../utils/occ";
import { LiveConfirmField } from "../trading/LiveConfirmField";
import { expiredToResend, loadDismissed, saveDismissed, structureFromOrder } from "./resend";
import { requestTicket } from "./ticketIntent";

const POLL_MS = 4_000;
// The expired list changes once a day, at the close: no need to poll it
// with the working one.
const EXPIRED_POLL_MS = 60_000;

/** The stock a resting package is written on. A multi-leg order carries no
 * symbol of its own (it is null on the parent), so it comes off the first
 * leg's contract; a single-leg order off its own. Null when neither parses
 * as a contract, in which case the row simply is not draggable. */
function underlyingOf(order: Order): string | null {
  const occ = order.legs?.find((leg) => leg.symbol)?.symbol ?? order.symbol;
  return occ ? (parseOcc(occ)?.underlying ?? null) : null;
}

/** The package's legs, with a single-leg order standing in as its own. */
function legsOf(order: Order): Order[] {
  return order.legs && order.legs.length > 0 ? order.legs : [order];
}

/** The contract a ⇧-drag carries: the package's own on a single-leg
 * order, its first leg's on a package (the long leg, the way the ticket
 * orders them). */
function contractOf(order: Order): string | null {
  const occ = legsOf(order).find((leg) => leg.symbol)?.symbol ?? null;
  return occ && parseOcc(occ) ? occ : null;
}

function ratioOf(leg: Order): number {
  const raw = Number(leg.ratio_qty ?? leg.qty ?? 1);
  return Number.isFinite(raw) && raw > 0 ? raw : 1;
}

/** The package's net price from live quotes, signed the way the backend
 * signs it (app/options/pricing.py net_price): positive is a debit you
 * pay, negative a credit you receive. `mid` prices every leg at its
 * midpoint; `natural` crosses the market -- buy legs at the ask, sell legs
 * at the bid -- and so is the price a marketable order would actually get.
 * Null when any leg is missing the quote it needs: a package price with a
 * hole in it is not a price. */
function netOf(order: Order, quotes: Record<string, LegQuote>, use: "mid" | "natural"): number | null {
  let total = 0;
  for (const leg of legsOf(order)) {
    const quote = leg.symbol ? quotes[leg.symbol] : undefined;
    if (!quote) return null;
    const buy = leg.side === "buy";
    const price = use === "mid" ? quote.mid : buy ? quote.ask : quote.bid;
    if (price == null || price <= 0) return null;
    total += (buy ? price : -price) * ratioOf(leg);
  }
  return Math.round(total * 10000) / 10000;
}

/** The limit in the same signed space as netOf.
 *
 * A multi-leg package already carries Alpaca's signed MLEG limit (positive
 * is the most to pay, negative the least to receive -- see backend
 * pricing.alpaca_limit), and its parent has no side of its own, so the
 * number is taken as it stands. A single-leg order's limit is a plain
 * positive price and its own side says which way it points. */
function signedLimit(order: Order): number | null {
  const raw = order.limit_price == null ? null : Number(order.limit_price);
  if (raw == null || !Number.isFinite(raw) || raw === 0) return null;
  const multiLeg = !!order.legs && order.legs.length > 0;
  if (multiLeg) return raw;
  return order.side === "buy" ? Math.abs(raw) : -Math.abs(raw);
}

/** Why the package is still resting, in one clause.
 *
 * Both sides reduce to the same comparison once the numbers are signed:
 * the order can fill when the natural has come down to the limit, i.e.
 * `natural <= limit`. For a debit that means the market stopped asking
 * more than you offer; for a credit, that it stopped bidding less than
 * you ask. The gap is what the market still has to travel, per share. */
function fillNote(order: Order, quotes: Record<string, LegQuote>): { text: string; title: string; reachable: boolean } | null {
  const limit = signedLimit(order);
  const natural = netOf(order, quotes, "natural");
  const mid = netOf(order, quotes, "mid");
  if (limit == null || natural == null) return null;
  const gap = Math.round((natural - limit) * 100) / 100;
  const money = (v: number) => Math.abs(v).toFixed(2);
  const midPart = mid == null ? "" : ` · mid ${money(mid)}`;
  const title =
    `Crossing the market right now would ${natural > 0 ? "cost" : "pay"} ${money(natural)} per share; your limit ` +
    `${limit > 0 ? "pays at most" : "asks at least"} ${money(limit)}. ` +
    (gap > 0
      ? `The market has ${gap.toFixed(2)} per share to travel before this package can fill. Quotes, not fills: a package fills when the two sides meet, and a wide chain can leave a limit resting all day.`
      : "The market is already at your limit; a fill should follow, and if it does not the quote may be stale or the size not there.");
  return {
    // "away from", not "short of": on a debit the market sits *above* the
    // limit and on a credit *below* it, and one word has to fit both.
    text: gap > 0 ? `natural ${money(natural)}${midPart} · ${gap.toFixed(2)} away from your limit` : `natural ${money(natural)}${midPart} · at your limit`,
    title,
    reachable: gap <= 0,
  };
}

/** The package in words, with each contract a drag source of its own.
 *
 * Dragging the row carries the underlying and shift-dragging the
 * contract, but a modifier is a poor thing to depend on: the browser
 * reads shift as "move" and the gesture has three ways to be swallowed
 * before a drop handler sees it. Dragging the contract you can see needs
 * no modifier at all. */
function Describe({ order }: { order: Order }) {
  const legs = order.legs && order.legs.length > 0 ? order.legs : [order];
  const signed = signedLimit(order);
  const price = signed == null ? "market" : `@ ${Math.abs(signed).toFixed(2)}`;
  const multi = !!order.legs && order.legs.length > 0;
  // Which way the package points comes from the signed limit, not from
  // `side`: a multi-leg parent has no side of its own, and reading it
  // there labelled every debit package "Receive".
  return (
    <>
      {signed == null ? (order.side === "buy" ? "Pay" : "Receive") : signed > 0 ? "Pay" : "Receive"} {price} ·{" "}
      {order.qty ?? "?"} ×{" "}
      {legs.map((leg, i) => (
        <span key={leg.symbol ?? i}>
          {i > 0 ? " " : ""}
          <span
            className="order-contract"
            title="Drag onto the chart for this contract's premium"
            {...(leg.symbol ? symbolDragProps(leg.symbol) : {})}
          >
            {multi ? (leg.side === "buy" ? "+" : "−") : ""}
            {formatLeg(leg.symbol ?? "")}
          </span>
        </span>
      ))}
    </>
  );
}

/** The account's resting option orders (a limit the market has not
 * reached): the simulated book's packages, or Alpaca's option orders in
 * Paper and Live -- so a put sold a minute ago that has not filled is
 * found here, next to the spreads, rather than only in the trading
 * widget's Orders tab. A cancel each; in Live the typed confirmation
 * first. Polled like the spreads; refetched on every replay tick.
 * Nothing to show means nothing rendered. */
export function OptionOrders({
  mode,
  onChanged,
  onSelectSymbol,
}: {
  mode: TradingMode;
  onChanged?: () => void;
  onSelectSymbol?: (symbol: string) => void;
}) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [closed, setClosed] = useState<Order[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(() => loadDismissed());
  const [quotes, setQuotes] = useState<Record<string, LegQuote>>({});
  const [error, setError] = useState<string | null>(null);
  const [liveTyped, setLiveTyped] = useState("");
  const replayAsOf = useReplaySession()?.as_of ?? null;

  const load = useCallback(() => {
    getOptionOrders("open")
      .then((res) => {
        setOrders(res.orders);
        setError(null);
        // Every contract in every resting package, priced once per poll:
        // the "why is this not filling" answer is the current market
        // against the limit, and that is a quote question, not a
        // trade-history one. A leg whose quote fails is simply left out,
        // and netOf then reports no price rather than half a price.
        const symbols = [...new Set(res.orders.flatMap((o) => legsOf(o).map((leg) => leg.symbol)).filter((s): s is string => !!s))];
        if (symbols.length === 0) {
          setQuotes({});
          return;
        }
        void Promise.all(
          symbols.map((symbol) =>
            getContractQuote(symbol)
              .then((quote) => [symbol, quote] as const)
              .catch(() => null),
          ),
        ).then((pairs) => {
          const next: Record<string, LegQuote> = {};
          for (const pair of pairs) if (pair) next[pair[0]] = pair[1];
          setQuotes(next);
        });
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load, replayAsOf, mode]);

  useEffect(() => {
    const loadClosed = () =>
      getOptionOrders("closed")
        .then((res) => setClosed(res.orders))
        .catch(() => setClosed([]));
    loadClosed();
    const id = window.setInterval(loadClosed, EXPIRED_POLL_MS);
    return () => window.clearInterval(id);
  }, [mode, replayAsOf]);

  const offered = expiredToResend(closed, orders, Date.now());
  const expired = offered.filter((o) => !dismissed.has(o.id));

  const dismiss = (order: Order) => {
    const next = new Set(dismissed).add(order.id);
    setDismissed(next);
    saveDismissed(next, offered);
  };

  const resend = (order: Order) => {
    const structure = structureFromOrder(order);
    if (!structure) return;
    onSelectSymbol?.(structure.ticket.underlying);
    requestTicket({ symbol: structure.ticket.underlying, structure });
  };

  if (orders.length === 0 && expired.length === 0 && !error) return null;

  return (
    <div className="option-orders">
      {orders.length > 0 && (
        <span className="option-orders-title" title="Option orders the market has not filled yet. A fill moves the contract into the spreads below.">
          Working packages
        </span>
      )}
      {error && <p className="order-rejection">{error}</p>}
      <ul className="spread-legs">
        {orders.map((order) => {
          const underlying = underlyingOf(order);
          const contract = contractOf(order);
          const fill = fillNote(order, quotes);
          return (
          // Drags onto the chart as its underlying; ⇧-drag as the contract
          // itself, which is the premium chart this package rests in.
          <li
            key={order.id}
            title="Drag onto the chart for the underlying; hold ⇧ while dragging for the contract's premium"
            {...packageDragProps(underlying, contract)}
          >
            <Describe order={order} />
            {order.time_in_force === "gtc" && (
              <span className="order-hint" title="Good till cancelled: rests past the close until it fills or you cancel it.">
                {" "}
                · GTC
              </span>
            )}
            {order.status && order.status !== "new" && order.status !== "accepted" ? <span className="order-hint"> · {order.status}</span> : null}
            {fill && (
              <span className={`order-hint order-fill-gap${fill.reachable ? " reachable" : ""}`} title={fill.title}>
                {" "}
                · {fill.text}
              </span>
            )}{" "}
            <button
              type="button"
              className={`row-action${mode === "live" ? " live-action" : ""}`}
              disabled={!liveConfirmed(mode, liveTyped)}
              title={mode === "live" && !liveConfirmed(mode, liveTyped) ? "Type LIVE below to cancel on the real account" : undefined}
              onClick={() => {
                cancelOptionOrder(order.id, mode === "live" ? liveTyped.trim() : undefined)
                  .then(() => {
                    load();
                    onChanged?.();
                  })
                  .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
              }}
            >
              Cancel
            </button>
          </li>
          );
        })}
      </ul>
      {expired.length > 0 && (
        <>
          <span
            className="option-orders-title"
            title="Day orders Alpaca cancelled at the close because they did not fill, from the last few days. Re-send loads the same strikes into the ticket at today's mid; nothing is sent until you place it there."
          >
            Expired at the close
          </span>
          <ul className="spread-legs">
            {expired.map((order) => {
              const structure = structureFromOrder(order);
              const at = order.expired_at ? new Date(order.expired_at) : null;
              return (
                <li key={order.id} className="order-expired">
                  <Describe order={order} />
                  {at && <span className="order-hint"> · expired {at.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}</span>}{" "}
                  <button
                    type="button"
                    className="row-action"
                    disabled={!structure}
                    title={
                      structure
                        ? "Load the same strikes into the ticket, priced at today's mid. Choose GTC there to let it rest past the close."
                        : "This shape cannot be loaded by strikes; rebuild it in the ticket's builder."
                    }
                    onClick={() => resend(order)}
                  >
                    Re-send
                  </button>{" "}
                  <button
                    type="button"
                    className="row-action"
                    title="Remove it from this list. Alpaca already cancelled the order at the close, so nothing is sent; the list forgets it in this browser."
                    onClick={() => dismiss(order)}
                  >
                    Dismiss
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
      <LiveConfirmField mode={mode} value={liveTyped} onChange={setLiveTyped} />
    </div>
  );
}
