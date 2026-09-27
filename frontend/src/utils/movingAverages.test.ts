import { describe, expect, it } from "vitest";

import { DEFAULT_MOVING_AVERAGES, parseMovingAverages, type MovingAverageLine } from "../api/settings";
import type { Bar, IndicatorResult } from "../types/alpaca";
import { ema, sma } from "./chartStudies";
import { applyMovingAverages, movingAverageSeries } from "./movingAverages";

const CLOSES = [10, 10.4, 10.2, 10.9, 11.3, 10.8, 11.1, 11.6, 11.2, 11.9, 12.3, 12.0];

function bars(): Bar[] {
  const start = Date.UTC(2026, 8, 21, 13, 30);
  return CLOSES.map((c, i) => ({ t: new Date(start + i * 60_000).toISOString(), o: c, h: c, l: c, c, v: 1 }));
}

function line(patch: Partial<MovingAverageLine> = {}): MovingAverageLine {
  return { enabled: true, length: 3, type: "ema", color: "#112233", width: 1, dash: "solid", ...patch };
}

function maIndicator(): IndicatorResult {
  // What the server sends: its own defaults, which the reader's setting replaces.
  return {
    name: "MA",
    kind: "series",
    series: { "EMA 9": [], "EMA 20": [], "SMA 50": [] },
    colors: { "EMA 9": "#2eb872" },
    study: { "EMA 9": { type: "ema", length: 9 } },
  };
}

describe("movingAverageSeries", () => {
  it("names, colours and computes each enabled line from the candles", () => {
    const { series, colors, study } = movingAverageSeries(bars(), [
      line({ length: 3, type: "ema", color: "#aabbcc" }),
      line({ length: 4, type: "sma", color: "#ddeeff" }),
    ]);
    expect(Object.keys(series)).toEqual(["EMA 3", "SMA 4"]);
    expect(colors).toEqual({ "EMA 3": "#aabbcc", "SMA 4": "#ddeeff" });
    expect(study["SMA 4"]).toEqual({ type: "sma", length: 4 });

    const points = series["SMA 4"] as { t: string; value: number | null }[];
    expect(points.map((p) => p.value)).toEqual(sma(CLOSES, 4));
    expect((series["EMA 3"] as typeof points).map((p) => p.value)).toEqual(ema(CLOSES, 3));
    expect(points.map((p) => p.t)).toEqual(bars().map((b) => b.t));
  });

  it("leaves a switched-off line out entirely", () => {
    const { series } = movingAverageSeries(bars(), [line({ enabled: false }), line({ length: 5 })]);
    expect(Object.keys(series)).toEqual(["EMA 5"]);
  });

  it("does not draw the same line twice", () => {
    const { series } = movingAverageSeries(bars(), [line({ length: 5 }), line({ length: 5 })]);
    expect(Object.keys(series)).toEqual(["EMA 5"]);
  });
});

describe("applyMovingAverages", () => {
  it("replaces the server's MA lines with the reader's", () => {
    const [ma] = applyMovingAverages([maIndicator()], bars(), [line({ length: 5, color: "#010203" })]);
    expect(Object.keys(ma.series)).toEqual(["EMA 5"]);
    expect(ma.colors).toEqual({ "EMA 5": "#010203" });
  });

  it("leaves other indicators and the array alone", () => {
    const level: IndicatorResult = { name: "Daily Range", kind: "level", series: { High: 1 }, colors: {} };
    const list = [level];
    expect(applyMovingAverages(list, bars(), DEFAULT_MOVING_AVERAGES)).toBe(list);
    const [, untouched] = applyMovingAverages([maIndicator(), level], bars(), DEFAULT_MOVING_AVERAGES);
    expect(untouched).toBe(level);
  });
});

describe("the stored setting", () => {
  it("falls back per field rather than dropping a line", () => {
    const parsed = parseMovingAverages([
      { enabled: false, length: 7, type: "sma", color: "not-a-colour", width: 9, dash: "wobbly" },
      { length: 9999 },
      "nonsense",
    ]);
    expect(parsed[0]).toEqual({
      ...DEFAULT_MOVING_AVERAGES[0],
      enabled: false,
      length: 7,
      type: "sma",
      // The colour was not a colour and the weight not one of the three.
      color: DEFAULT_MOVING_AVERAGES[0].color,
    });
    expect(parsed[1].length).toBe(400); // clamped to the picker's ceiling
    expect(parsed[2]).toEqual(DEFAULT_MOVING_AVERAGES[2]);
  });

  it("answers with the defaults for anything else", () => {
    expect(parseMovingAverages(undefined)).toEqual(DEFAULT_MOVING_AVERAGES);
    expect(parseMovingAverages({})).toEqual(DEFAULT_MOVING_AVERAGES);
  });
});


describe("telling the lines apart", () => {
  it("gives each line its own weight and dash", () => {
    const { styles } = movingAverageSeries(bars(), [
      line({ length: 5, width: 1, dash: "solid" }),
      line({ length: 8, width: 3, dash: "dashed" }),
    ]);
    expect(styles["EMA 5"]).toEqual({ width: 1, dash: "solid" });
    expect(styles["EMA 8"]).toEqual({ width: 3, dash: "dashed" });
  });

  it("carries them onto the indicator the chart draws", () => {
    const [ma] = applyMovingAverages([maIndicator()], bars(), [line({ length: 7, width: 2, dash: "dotted" })]);
    expect(ma.styles).toEqual({ "EMA 7": { width: 2, dash: "dotted" } });
  });

  it("defaults tell the fast line from the slow one without colour", () => {
    const [fast, mid, slow] = DEFAULT_MOVING_AVERAGES;
    expect(fast.width).toBeLessThan(mid.width);
    expect(slow.dash).toBe("dashed");
  });
});
