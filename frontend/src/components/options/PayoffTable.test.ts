import { describe, expect, it } from "vitest";

import { nearestColumn } from "./PayoffTable";

describe("the Time slider's column in the payoff table", () => {
  const day = 24 * 3600 * 1000;
  const dates = [0, day, 2 * day, 5 * day];
  it("marks the column whose close lies nearest the slider's moment", () => {
    expect(nearestColumn(dates, 0)).toBe(0);
    expect(nearestColumn(dates, 1.4 * day)).toBe(1);
    expect(nearestColumn(dates, 1.6 * day)).toBe(2);
    expect(nearestColumn(dates, 4 * day)).toBe(3);
    expect(nearestColumn(dates, 9 * day)).toBe(3);
  });
});
