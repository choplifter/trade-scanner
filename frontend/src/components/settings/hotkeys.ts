/** Every keyboard shortcut the dashboard has, for the Settings dialog's
 * Hotkeys tab. Kept as data here rather than read off the components:
 * the bindings live in three places (OrderTicket, TradingWidget,
 * OptionsWidget) and this is the one list a reader can scan. */

export interface HotkeyGroup {
  title: string;
  note?: string;
  keys: { keys: string; action: string }[];
}

export const HOTKEY_GROUPS: HotkeyGroup[] = [
  {
    title: "Order ticket (stocks)",
    note: "Suppressed while typing in a field or while the confirm dialog is open.",
    keys: [
      { keys: "B / S", action: "Side: buy / sell" },
      { keys: "1 – 4", action: "Order type: market, limit, stop, stop-limit" },
      { keys: "Enter", action: "Open the confirm dialog" },
      { keys: "Esc", action: "Close the confirm dialog" },
    ],
  },
  {
    title: "Instant-fire (stocks, Paper and Simulation only)",
    note: "One keypress, no confirm dialog; the server re-checks every ceiling. Off in Live.",
    keys: [
      { keys: "Q / W / E", action: "Buy at 0.5% / 1% / 2% equity risk off the ticket's Stop (Shift = sell)" },
      { keys: "T", action: "Breakout entry: buy-stop at the day's high + $0.01, risk-sized" },
      { keys: "F", action: "Flatten the position on the selected symbol" },
      { keys: "C", action: "Cancel every working order" },
      { keys: "0", action: "Move the stop to breakeven" },
      { keys: "Shift + 0", action: "Stop to breakeven plus a $0.05 buffer" },
      { keys: "X", action: "Scale out 50% at market" },
    ],
  },
  {
    title: "Options widget (focus inside the widget)",
    note: "Off in Live. The outright longs and the newer strategies have no key: 0–4 belong to the stock ticket.",
    keys: [
      { keys: "[ / ]", action: "Previous / next expiry" },
      { keys: "5 – 9", action: "Bull call, bear put, bull put, bear call, iron condor" },
      { keys: "+ / −", action: "Width (strikes between the legs) up / down" },
      { keys: "Shift + + / −", action: "Short distance (delta or strikes from the spot) further out / closer in" },
    ],
  },
  {
    title: "Chart",
    keys: [
      { keys: "Esc", action: "Leave fullscreen" },
      { keys: "Right-click a Dock tab", action: "Open in new window / Float / Close" },
      { keys: "Drag a symbol cell", action: "Drop on a chart, a chart copy, the Options widget or the watchlist" },
    ],
  },
];
