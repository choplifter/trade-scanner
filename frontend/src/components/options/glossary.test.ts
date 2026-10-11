import { describe, expect, it } from "vitest";

import { glossFor, withGloss } from "./glossary";

const first = (label: string) => glossFor(label).map((t) => t.split(" = ")[0]);

describe("the abbreviation glossary", () => {
  it("names each abbreviation a label uses, and only those", () => {
    expect(first("EV")).toEqual(["EV"]);
    expect(first("IV/RV")).toEqual(["IV", "RV"]);
    expect(first("IV rank")).toEqual(["IV rank"]);
    expect(first("BE vol")).toEqual(["BE vol"]);
    expect(first("To BE")).toEqual(["BE"]);
    expect(first("Vol/OI")).toEqual(["OI", "Vol"]);
    expect(first("Worst leg")).toEqual([]);
    expect(first("Loss : Gain")).toEqual([]);
    expect(first("Expiry")).toEqual([]);
  });

  it("puts the long forms in front of the tooltip", () => {
    expect(withGloss("EV", "Sorts by it.")).toMatch(/^EV = expected value.*\n\nSorts by it\.$/s);
    expect(withGloss("Expiry", "Sorts by days.")).toBe("Sorts by days.");
  });
});
