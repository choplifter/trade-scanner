import { describe, expect, it } from "vitest";

import type { Bar, IndicatorResult } from "../types/alpaca";
import { deriveMarkers, moveMarkers, moveThreshold, type MoveRule } from "./moveMarkers";

// backend/app/indicators/ten_year_moves.py's DERIVE.
const RULE: MoveRule = { type: "moves", label: "10Y", unit: "bp", per_unit: 100, daily_sigma: 7, k: 1.5, floor: 2 };

const T0 = Date.UTC(2026, 8, 18, 13, 30);
const at = (minutes: number) => new Date(T0 + minutes * 60_000).toISOString();

function candles(count: number, minutes: number): Bar[] {
  return Array.from({ length: count }, (_, i) => ({ t: at(i * minutes), o: 1, h: 1, l: 1, c: 1, v: 1 }));
}

describe("the threshold scales with the candle", () => {
  it.each([
    [1, 2],
    [15, 2.06],
    [60, 4.12],
    [390, 10.5],
    [1950, 23.48],
  ])("%i-minute candles: %f bp", (minutes, bp) => {
    expect(moveThreshold(RULE, minutes)).toBeCloseTo(bp, 2);
  });
});

describe("moveMarkers", () => {
  it("marks a candle by its own close against the close before it", () => {
    const bars = candles(3, 15);
    // 4.95 before, 4.98 inside candle 1 (+3 bp), flat through candle 2.
    const points = [
      { t: at(-1), value: 4.95 },
      { t: at(5), value: 4.97 },
      { t: at(14), value: 4.98 },
      { t: at(20), value: 4.985 },
    ];
    const { rising, falling } = moveMarkers(bars, points, RULE, 15);
    expect(rising).toEqual([{ time: T0 / 1000, position: "aboveBar", shape: "arrowUp", text: "10Y +3bp" }]);
    expect(falling).toEqual([]);
  });

  it("marks a fall below the candle", () => {
    const bars = candles(1, 15);
    const { falling } = moveMarkers(bars, [{ t: at(-1), value: 5 }, { t: at(10), value: 4.96 }], RULE, 15);
    expect(falling).toEqual([{ time: T0 / 1000, position: "belowBar", shape: "arrowDown", text: "10Y -4bp" }]);
  });

  it("leaves a move under the threshold alone", () => {
    const bars = candles(1, 60);
    const { rising, falling } = moveMarkers(bars, [{ t: at(-1), value: 5 }, { t: at(30), value: 5.03 }], RULE, 60);
    expect([...rising, ...falling]).toEqual([]);
  });

  it("gives a candle with no data no marker, and bridges the gap for the next", () => {
    const bars = candles(3, 15);
    // Nothing inside candle 1; candle 2 compares with the last value seen.
    const points = [
      { t: at(-1), value: 5 },
      { t: at(5), value: 5 },
      { t: at(40), value: 5.05 },
    ];
    const { rising } = moveMarkers(bars, points, RULE, 15);
    expect(rising.map((m) => m.time)).toEqual([T0 / 1000 + 30 * 60]);
  });

  it("needs a value before the first candle to mark it", () => {
    const { rising } = moveMarkers(candles(1, 15), [{ t: at(5), value: 9 }], RULE, 15);
    expect(rising).toEqual([]);
  });
});

describe("deriveMarkers", () => {
  it("turns the point series into Rising/Falling markers and leaves the rest", () => {
    const yieldInd: IndicatorResult = {
      name: "10Y Moves",
      kind: "marker",
      series: { "10Y": [{ t: at(-1), value: 5 }, { t: at(5), value: 5.1 }] },
      colors: {},
      derive: RULE,
    };
    const level: IndicatorResult = { name: "Daily Range", kind: "level", series: { High: 1 }, colors: {} };
    const [derived, untouched] = deriveMarkers([yieldInd, level], candles(1, 15), 15);
    expect(Object.keys(derived.series)).toEqual(["Rising", "Falling"]);
    expect(untouched).toBe(level);
  });

  it("hands back the same array when nothing needs deriving", () => {
    const list: IndicatorResult[] = [{ name: "Daily Range", kind: "level", series: { High: 1 }, colors: {} }];
    expect(deriveMarkers(list, candles(1, 15), 15)).toBe(list);
  });
});
