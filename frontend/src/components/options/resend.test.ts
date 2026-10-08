import { describe, expect, it } from "vitest";

import type { Order } from "../../types/trading";
import { expiredToResend, loadDismissed, saveDismissed, structureFromOrder } from "./resend";

const leg = (symbol: string, side: "buy" | "sell", intent = `${side}_to_open`) =>
  ({ symbol, side, ratio_qty: "1", position_intent: intent }) as unknown as Order;

const condor = (id: string, status: string, expiredAt: string | null, closing = false) =>
  ({
    id,
    symbol: null,
    qty: "2",
    status,
    expired_at: expiredAt,
    submitted_at: expiredAt,
    legs: [
      closing ? leg("TLT261120P00071000", "sell", "sell_to_close") : leg("TLT261120P00071000", "buy"),
      leg("TLT261120P00072000", "sell"),
      leg("TLT261120C00079000", "sell"),
      leg("TLT261120C00080000", "buy"),
    ],
  }) as unknown as Order;

const pair = (a: Order, b: Order) => ({ id: "v", qty: "1", legs: [a, b] }) as unknown as Order;

describe("re-sending an expired package", () => {
  it("turns a condor's contracts back into the ticket's iron condor, without the old limit", () => {
    expect(structureFromOrder(condor("a", "expired", null))).toEqual({
      strategy: "iron_condor",
      ticket: {
        underlying: "TLT",
        expiry: "2026-11-20",
        qty: 2,
        strategy: "iron_condor",
        put_long_strike: 71,
        put_short_strike: 72,
        call_short_strike: 79,
        call_long_strike: 80,
      },
    });
  });

  it("reads the verticals and a long option, and gives up on other shapes", () => {
    expect(structureFromOrder(pair(leg("IEF261120P00086000", "buy"), leg("IEF261120P00087000", "sell")))?.strategy).toBe("bull_put");
    expect(structureFromOrder(pair(leg("IEF261120P00087000", "buy"), leg("IEF261120P00086000", "sell")))?.strategy).toBe("bear_put");
    expect(structureFromOrder(pair(leg("IEF261120C00091000", "sell"), leg("IEF261120C00092000", "buy")))?.strategy).toBe("bear_call");
    expect(structureFromOrder(pair(leg("IEF261120C00091000", "buy"), leg("IEF261120C00092000", "sell")))?.strategy).toBe("bull_call");
    const single = { id: "s", symbol: "IEF261120C00091000", side: "buy", qty: "3", legs: null } as unknown as Order;
    expect(structureFromOrder(single)?.ticket).toMatchObject({ strategy: "long_call", long_strike: 91, qty: 3 });
    expect(structureFromOrder(pair(leg("IEF261120P00086000", "sell"), leg("IEF261120C00092000", "sell")))).toBeNull();
  });

  it("offers the recent expired openings once each, and not what rests again or was a close", () => {
    const now = Date.parse("2026-10-08T12:00:00Z");
    const closed = [
      condor("new", "expired", "2026-10-07T20:00:02Z"),
      condor("old", "expired", "2026-10-06T20:00:02Z"),
      condor("stale", "expired", "2026-09-20T20:00:02Z"),
      condor("cancelled", "canceled", "2026-10-07T19:20:00Z"),
      condor("close", "expired", "2026-10-07T20:00:02Z", true),
    ];
    expect(expiredToResend(closed, [], now).map((o) => o.id)).toEqual(["new"]);
    expect(expiredToResend(closed, [condor("again", "new", "2026-10-08T14:00:00Z")], now)).toEqual([]);
    // Re-sent and filled: no longer working, but the package is not offered again.
    const filled = condor("filled", "filled", "2026-10-08T14:00:00Z");
    expect(expiredToResend([...closed, filled], [], now)).toEqual([]);
  });
});

describe("dismissing an expired package", () => {
  it("remembers the ids still listed and forgets the rest; a blocked storage shows everything", () => {
    const data: Record<string, string> = {};
    const g = globalThis as unknown as { window?: unknown };
    g.window = { localStorage: { getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => (data[k] = v) } };
    saveDismissed(new Set(["a", "gone"]), [condor("a", "expired", null), condor("b", "expired", null)]);
    expect([...loadDismissed()]).toEqual(["a"]);
    g.window = {
      localStorage: {
        getItem: () => {
          throw new Error("blocked");
        },
        setItem: () => {
          throw new Error("blocked");
        },
      },
    };
    expect(loadDismissed().size).toBe(0);
    expect(() => saveDismissed(new Set(["a"]), [])).not.toThrow();
    delete g.window;
  });
});
