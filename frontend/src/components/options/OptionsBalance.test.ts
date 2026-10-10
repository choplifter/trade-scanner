import { describe, expect, it } from "vitest";

import type { ClosedSpread } from "../../types/options";
import { cumulativePoints } from "./OptionsBalance";

const spread = (opened: string, closed: string, pnl: number) => ({ opened_at: opened, closed_at: closed, pnl }) as unknown as ClosedSpread;

describe("the options' realised P/L over time", () => {
  it("starts at nothing and adds each close in order", () => {
    const points = cumulativePoints([
      spread("2026-10-06T13:50:00Z", "2026-10-06T14:02:00Z", 156),
      spread("2026-10-05T13:54:00Z", "2026-10-05T13:54:52Z", 23),
      spread("2026-10-08T14:00:00Z", "2026-10-08T16:17:00Z", -54),
    ]);
    expect(points.map((p) => p.equity)).toEqual([0, 23, 179, 125]);
    expect(points[0].t).toBe(Date.parse("2026-10-05T13:54:52Z") / 1000 - 60);
  });

  it("moves spreads settled in the same second apart, and has no curve without a spread", () => {
    const points = cumulativePoints([
      spread("2026-10-09T14:40:00Z", "2026-10-09T20:00:00Z", 108),
      spread("2026-10-09T16:32:00Z", "2026-10-09T20:00:00Z", 140),
    ]);
    const times = points.map((p) => p.t);
    expect(new Set(times).size).toBe(times.length);
    expect(points[points.length - 1].equity).toBe(248);
    expect(cumulativePoints([])).toEqual([]);
  });
});
