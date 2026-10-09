import { describe, expect, it } from "vitest";

import { DEFAULT_FORM, requestFromForm } from "./ZeroDtePanel";

describe("the 0DTE backtest form", () => {
  it("sends the backend's defaults, with blanks as none and no dates", () => {
    expect(requestFromForm(DEFAULT_FORM)).toEqual({
      entry: "10:00",
      short_delta: 0.1,
      width: 2,
      stop_mult: 2,
      take_profit: null,
      slippage: 0.01,
      fee: 0.03,
    });
  });

  it("turns a target in per cent into a share, keeps dates, and refuses bad numbers", () => {
    const req = requestFromForm({ ...DEFAULT_FORM, stop: "", target: "50", start: "2025-01-02", end: "2025-06-30" });
    expect(req).toMatchObject({ stop_mult: null, take_profit: 0.5, start: "2025-01-02", end: "2025-06-30" });
    expect(requestFromForm({ ...DEFAULT_FORM, shortDelta: "0.7" })).toBeNull();
    expect(requestFromForm({ ...DEFAULT_FORM, stop: "1" })).toBeNull();
    expect(requestFromForm({ ...DEFAULT_FORM, width: "" })).toBeNull();
    expect(requestFromForm({ ...DEFAULT_FORM, slippage: "-0.01" })).toBeNull();
  });
});
