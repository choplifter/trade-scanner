import { describe, expect, it } from "vitest";

import type { ZeroDteTrade } from "../../types/playbooks";
import { DEFAULT_FORM, filterTrades, requestFromForm } from "./ZeroDtePanel";

describe("the 0DTE backtest form", () => {
  it("starts held to the close: no stop, no target, no dates", () => {
    expect(requestFromForm(DEFAULT_FORM)).toEqual({
      entry: "10:00",
      short_delta: 0.1,
      width: 2,
      stop_mult: null,
      take_profit: null,
      slippage: 0.01,
      fee: 0.03,
    });
  });

  it("turns a target in per cent into a share, keeps dates, and refuses bad numbers", () => {
    const req = requestFromForm({ ...DEFAULT_FORM, stop: "", target: "50", start: "2025-01-02", end: "2025-06-30" });
    expect(req).toMatchObject({ stop_mult: null, take_profit: 0.5, start: "2025-01-02", end: "2025-06-30" });
    expect(requestFromForm({ ...DEFAULT_FORM, shortDelta: "0.7" })).toBeNull();
    expect(requestFromForm({ ...DEFAULT_FORM, stop: "2" })?.stop_mult).toBe(2);
    expect(requestFromForm({ ...DEFAULT_FORM, stop: "1" })).toBeNull();
    expect(requestFromForm({ ...DEFAULT_FORM, width: "" })).toBeNull();
    expect(requestFromForm({ ...DEFAULT_FORM, slippage: "-0.01" })).toBeNull();
  });
});

describe("the 0DTE trade list", () => {
  const t = (day: string, pnl: number, exit: "stop" | "expiry" = "expiry", pinned = false) =>
    ({ day, pnl, exit, pinned }) as unknown as ZeroDteTrade;
  const trades = [t("2025-01-02", 5), t("2025-01-03", -120, "expiry", true), t("2025-01-06", -9, "stop"), t("2025-01-07", 6)];

  it("filters and orders the trades", () => {
    expect(filterTrades(trades, "all", "date").map((x) => x.day)).toEqual(["2025-01-07", "2025-01-06", "2025-01-03", "2025-01-02"]);
    expect(filterTrades(trades, "losses", "pnl").map((x) => x.pnl)).toEqual([-120, -9]);
    expect(filterTrades(trades, "stops", "date").map((x) => x.day)).toEqual(["2025-01-06"]);
    expect(filterTrades(trades, "pinned", "date").map((x) => x.day)).toEqual(["2025-01-03"]);
  });
});
