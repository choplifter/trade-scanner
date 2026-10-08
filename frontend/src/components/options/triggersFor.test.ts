import { describe, expect, it } from "vitest";

import type { SpreadGroup, UnderlyingTrigger } from "../../types/options";
import { triggersFor } from "./OpenSpreads";

const group = (id: string, symbols: string[]) =>
  ({ id, underlying: "TLT", expiry: "2026-11-20", legs: symbols.map((symbol) => ({ symbol })) }) as unknown as SpreadGroup;

const trigger = (id: string, symbols: string[]) =>
  ({ id, underlying: "TLT", expiry: "2026-11-20", status: "active", legs: symbols.map((symbol) => ({ symbol, qty: 1 })) }) as unknown as UnderlyingTrigger;

describe("which spread a trigger belongs to", () => {
  const bearPut = group("TLT:2026-11-20", ["TLT261120P00073000", "TLT261120P00078000"]);
  const condor = group("TLT:2026-11-20:1", ["TLT261120P00074000", "TLT261120P00075000", "TLT261120C00082000", "TLT261120C00083000"]);
  const onBearPut = trigger("t1", ["TLT261120P00073000", "TLT261120P00078000"]);

  it("is the spread whose legs it closes, not every spread on the same stock and expiry", () => {
    expect(triggersFor(bearPut, [onBearPut]).map((t) => t.id)).toEqual(["t1"]);
    expect(triggersFor(condor, [onBearPut])).toEqual([]);
  });

  it("takes a trigger on one side of a condor as the condor's", () => {
    const putSide = trigger("t2", ["TLT261120P00074000", "TLT261120P00075000"]);
    expect(triggersFor(condor, [putSide]).map((t) => t.id)).toEqual(["t2"]);
  });
});
