import { describe, expect, it } from "vitest";

import type { ChainResponse } from "../types/options";
import { atmIv, chainOf } from "./atmIv";

const chain = (underlying: string, iv: number): ChainResponse =>
  ({
    underlying,
    expiry: "2026-11-13",
    spot: 100,
    feed: "opra",
    as_of: "2026-10-01T18:11:00Z",
    rows: [{ strike: 100, call: { iv, bid: 1 }, put: { iv, bid: 1 } }],
  }) as unknown as ChainResponse;

describe("chainOf", () => {
  it("hands back the chain only for its own symbol", () => {
    // The render right after switching TLT -> AMZN: AMZN selected, TLT's
    // chain still held. Its 17 % must not be read as AMZN's.
    const tlt = chain("TLT", 0.17);
    expect(atmIv(chainOf("AMZN", tlt))).toBeNull();
    expect(atmIv(chainOf("TLT", tlt))).toBeCloseTo(0.17);
    expect(chainOf("tlt", tlt)).toBe(tlt);
    expect(chainOf(null, tlt)).toBeNull();
    expect(chainOf("TLT", null)).toBeNull();
  });
});

describe("atmIv", () => {
  it("reads only bid quotes", () => {
    const c = {
      underlying: "X", expiry: "2026-11-20", spot: 100.6, feed: "opra", as_of: "",
      rows: [
        { strike: 100, call: { iv: 0.9, bid: 0 }, put: { iv: 0.3, bid: 0.95 } },
        { strike: 101, call: { iv: 0.8, bid: null }, put: { iv: 0.85, bid: 0 } },
      ],
    } as unknown as ChainResponse;
    expect(atmIv(c)).toBeCloseTo(0.3);
  });
});
