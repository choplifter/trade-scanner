/** The chart colour schemes the Settings dialog offers. A handful of
 * proven palettes rather than per-candle colour pickers: each one sets
 * the up/down pair that candles, wicks, volume, the position lines, the
 * risk chart's areas and the tables' delta colours all share, in a light
 * and a dark variant (monochrome in particular needs both). */

export type ChartThemeId = "classic" | "tradingview" | "monochrome" | "colorblind" | "muted";

export interface ChartPalette {
  /** Candle body / wick of a rising bar, and the "favourable" colour. */
  up: string;
  /** Candle body / wick of a falling bar, and the "unfavourable" colour. */
  down: string;
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

function rgba(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
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

export function chartTheme(id: ChartThemeId): ChartTheme {
  return CHART_THEMES.find((t) => t.id === id) ?? CHART_THEMES[0];
}
