/** The chart colour schemes the Settings dialog offers: a handful of
 * proven palettes, plus "Custom" -- your own up/down colours for candle
 * bodies, wicks, volume and the tables' change colours (a TradingView
 * user brings their own scheme). Each palette sets the up/down pair that
 * candles, wicks, volume, the position lines, the risk chart's areas and
 * the tables' delta colours all share, in a light and a dark variant
 * (monochrome in particular needs both). */

export type ChartThemeId = "classic" | "tradingview" | "monochrome" | "colorblind" | "muted" | "custom";

export interface ChartPalette {
  /** Candle body of a rising bar, and the "favourable" colour. */
  up: string;
  /** Candle body of a falling bar, and the "unfavourable" colour. */
  down: string;
  /** Wick and border colours; the body colours when absent. */
  wickUp?: string;
  wickDown?: string;
  /** Volume columns (translucent). */
  volumeUp: string;
  volumeDown: string;
  /** Soft fills for the risk chart's profit / loss areas. */
  upSoft: string;
  downSoft: string;
  /** The table text colour for a positive / negative change; a darker green
   * on a light page reads better than the candle green. */
  deltaUp: string;
  deltaDown: string;
  /** Monochrome draws rising candles hollow regardless of the style setting. */
  forceHollow?: boolean;
}

export interface ChartTheme {
  id: ChartThemeId;
  label: string;
  description: string;
  light: ChartPalette;
  dark: ChartPalette;
}

/** The editable scheme: eight colours, used for light and dark alike. */
export interface CustomColors {
  up: string;
  down: string;
  wickUp: string;
  wickDown: string;
  volumeUp: string;
  volumeDown: string;
  deltaUp: string;
  deltaDown: string;
}

export const CUSTOM_COLOR_FIELDS: { key: keyof CustomColors; label: string; hint: string }[] = [
  { key: "up", label: "Up candle", hint: "Body of a rising candle; also position targets and the risk chart's profit area." },
  { key: "down", label: "Down candle", hint: "Body of a falling candle; also stops and the loss area." },
  { key: "wickUp", label: "Up wick", hint: "Wick and border of a rising candle." },
  { key: "wickDown", label: "Down wick", hint: "Wick and border of a falling candle." },
  { key: "volumeUp", label: "Up volume", hint: "Volume column under a rising candle (drawn translucent)." },
  { key: "volumeDown", label: "Down volume", hint: "Volume column under a falling candle." },
  { key: "deltaUp", label: "Positive text", hint: "Gains in the tables, P&L and the risk chart's legend." },
  { key: "deltaDown", label: "Negative text", hint: "Losses in the tables and P&L." },
];

export function rgba(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function isHexColor(value: unknown): value is string {
  return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value);
}

function palette(up: string, down: string, deltaUp = up, deltaDown = down, forceHollow = false): ChartPalette {
  return {
    up,
    down,
    volumeUp: rgba(up, 0.5),
    volumeDown: rgba(down, 0.5),
    upSoft: rgba(up, 0.14),
    downSoft: rgba(down, 0.14),
    deltaUp,
    deltaDown,
    forceHollow,
  };
}

export const CHART_THEMES: ChartTheme[] = [
  {
    id: "classic",
    label: "Classic",
    description: "Green up, red down -- the dashboard's original colours.",
    light: palette("#0ca30c", "#d03b3b", "#006300", "#d03b3b"),
    dark: palette("#0ca30c", "#e66767"),
  },
  {
    id: "tradingview",
    label: "TradingView",
    description: "Teal and coral, as TradingView draws them.",
    light: palette("#26a69a", "#ef5350", "#1e8a80", "#e53935"),
    dark: palette("#26a69a", "#ef5350"),
  },
  {
    id: "monochrome",
    label: "Monochrome",
    description: "Hollow rising candles, filled falling ones; no colour at all.",
    light: palette("#111111", "#111111", "#1f5f2a", "#8a1f1f", true),
    dark: palette("#e6e6e6", "#8c8c8c", "#7fd48a", "#e08a8a", true),
  },
  {
    id: "colorblind",
    label: "Colour-blind",
    description: "Blue up, orange down -- distinct for red-green colour vision.",
    light: palette("#1f77b4", "#ff7f0e", "#155f8f", "#c65f00"),
    dark: palette("#4da3e0", "#ffa040"),
  },
  {
    id: "muted",
    label: "Muted",
    description: "Softer green and red for long sessions.",
    light: palette("#4caf7d", "#d9534f", "#2e7a55", "#c0392b"),
    dark: palette("#6fbf95", "#e0736f"),
  },
];

/** What the Custom tile starts from: TradingView's pair, every field
 * spelled out so each picker has a value. */
export const DEFAULT_CUSTOM_COLORS: CustomColors = {
  up: "#26a69a",
  down: "#ef5350",
  wickUp: "#26a69a",
  wickDown: "#ef5350",
  volumeUp: "#26a69a",
  volumeDown: "#ef5350",
  deltaUp: "#1e8a80",
  deltaDown: "#e53935",
};

export const CUSTOM_THEME_META = {
  id: "custom" as const,
  label: "Custom",
  description: "Your own colours for candles, wicks, volume and the tables.",
};

/** A preset's colours as a starting point for the custom editor. */
export function customColorsFrom(theme: ChartTheme, dark: boolean): CustomColors {
  const p = dark ? theme.dark : theme.light;
  return {
    up: p.up,
    down: p.down,
    wickUp: p.wickUp ?? p.up,
    wickDown: p.wickDown ?? p.down,
    volumeUp: p.up,
    volumeDown: p.down,
    deltaUp: p.deltaUp,
    deltaDown: p.deltaDown,
  };
}

export function customPalette(colors: CustomColors): ChartPalette {
  return {
    up: colors.up,
    down: colors.down,
    wickUp: colors.wickUp,
    wickDown: colors.wickDown,
    volumeUp: rgba(colors.volumeUp, 0.5),
    volumeDown: rgba(colors.volumeDown, 0.5),
    upSoft: rgba(colors.up, 0.14),
    downSoft: rgba(colors.down, 0.14),
    deltaUp: colors.deltaUp,
    deltaDown: colors.deltaDown,
    forceHollow: false,
  };
}

/** A preset by id; "custom" (whose palette lives in the settings) falls
 * back to Classic here -- callers that care read getPalette() instead. */
export function chartTheme(id: ChartThemeId): ChartTheme {
  return CHART_THEMES.find((t) => t.id === id) ?? CHART_THEMES[0];
}
