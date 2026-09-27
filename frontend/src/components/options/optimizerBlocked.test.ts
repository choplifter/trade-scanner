import { describe, expect, it } from "vitest";

import { optimizerBlockedReason, type BlockedInput } from "./optimizerBlocked";

/** A panel that could run: every reason below is one field away from it. */
const READY: BlockedInput = {
  symbol: "SPY",
  expiryCount: 33,
  chainLoading: false,
  horizonExpiry: "2026-10-23",
  familyCount: 4,
  target: 790,
  spot: 771.35,
  running: false,
};

const reason = (patch: Partial<BlockedInput>) => optimizerBlockedReason({ ...READY, ...patch });

describe("why Find structures is disabled", () => {
  it("says nothing when the run can go ahead, or is already going", () => {
    expect(reason({})).toBeNull();
    expect(reason({ running: true, target: null })).toBeNull();
  });

  it("names the symbol that has no options at all", () => {
    // The case that started this: the outlook buttons look usable, the
    // button is dead, and nothing on screen says why.
    expect(reason({ symbol: "AIFF", expiryCount: 0, spot: null, target: null })).toBe(
      "AIFF has no listed options, so there is nothing to build from.",
    );
  });

  it("tells a chain still loading from a symbol with no options", () => {
    expect(reason({ expiryCount: 0, chainLoading: true, target: null, spot: null })).toBe("Loading the option chain…");
  });

  it("asks for a target once the chain is in", () => {
    expect(reason({ target: null })).toBe("Pick an outlook, or type a target price.");
  });

  it("says the outlook will fill the target itself while the spot is missing", () => {
    expect(reason({ target: null, spot: null })).toMatch(/set a target as soon as it is in/);
  });

  it("covers the rest of the panel", () => {
    expect(reason({ symbol: null })).toMatch(/Pick a symbol/);
    expect(reason({ horizonExpiry: "" })).toMatch(/horizon expiry/);
    expect(reason({ familyCount: 0 })).toMatch(/strategy family/);
  });
});
