/** The app's user settings: one object in localStorage, a module-level
 * store with subscribers (the pattern api/tradingMode.ts uses), so every
 * chart instance and widget sees a change made in the Settings dialog
 * without being its descendant. Applying the colour mode and the chart
 * palette to the document (data-theme attribute, CSS variables) happens
 * here too, so the tables and the risk chart follow the same values the
 * charts read. */

import {
  chartTheme,
  customPalette,
  DEFAULT_CUSTOM_COLORS,
  isHexColor,
  type ChartPalette,
  type ChartThemeId,
  type CustomColors,
} from "./chartTheme";
import {
  DEFAULT_SHORT_TARGETS,
  SHORT_DELTA_MAX,
  SHORT_DELTA_MIN,
  SHORT_OFFSET_MAX,
  type ShortTarget,
  type ShortTargetGroup,
} from "../types/options";

export type ColorMode = "system" | "light" | "dark";
export type CandleStyle = "filled" | "hollow";
export type NumberFormat = "auto" | "point" | "comma";
export type VwapAnchor = "session" | "premarket";
export type DefaultChartType = "candles" | "line";

export interface AppSettings {
  chartTheme: ChartThemeId;
  candleStyle: CandleStyle;
  sessionShading: boolean;
  colorMode: ColorMode;
  defaultTimeframe: string;
  defaultChartType: DefaultChartType;
  autoScroll: boolean;
  vwapAnchor: VwapAnchor;
  numberFormat: NumberFormat;
  /** Height of the risk chart (payoff diagram) in px; dragged in place. */
  riskChartHeight: number;
  /** Width of the Options widget's ticket column in px; dragged at the
   * splitter between the chain and the ticket. */
  optionsTicketWidth: number;
  /** The "Custom" scheme's colours (used when chartTheme is "custom"). */
  customColors: CustomColors;
  /** How far out the options auto-pick puts short legs, per strategy
   * group (condor / credit vertical & writes / strangle). */
  optionsShortTargets: Record<ShortTargetGroup, ShortTarget>;
  /** What the option tickets prefill as the limit: the mid (better price,
   * often rests on paper) or the natural (fills at once). */
  optionsLimitMode: "mid" | "natural";
}

export const DEFAULT_SETTINGS: AppSettings = {
  chartTheme: "classic",
  candleStyle: "filled",
  sessionShading: true,
  colorMode: "system",
  defaultTimeframe: "5m",
  defaultChartType: "candles",
  autoScroll: true,
  vwapAnchor: "session",
  numberFormat: "auto",
  riskChartHeight: 200,
  optionsTicketWidth: 360,
  customColors: { ...DEFAULT_CUSTOM_COLORS },
  optionsShortTargets: {
    condor: { ...DEFAULT_SHORT_TARGETS.condor },
    vertical: { ...DEFAULT_SHORT_TARGETS.vertical },
    strangle: { ...DEFAULT_SHORT_TARGETS.strangle },
  },
  optionsLimitMode: "natural",
};

/** One short target from storage, clamped to the picker's range. */
export function clampShortTarget(target: ShortTarget): ShortTarget {
  if (target.mode === "offset") {
    return { mode: "offset", value: Math.min(SHORT_OFFSET_MAX, Math.max(0, Math.round(target.value))) };
  }
  const value = Math.round(target.value * 20) / 20;
  return { mode: "delta", value: Math.min(SHORT_DELTA_MAX, Math.max(SHORT_DELTA_MIN, value)) };
}

function parseShortTargets(value: unknown): Record<ShortTargetGroup, ShortTarget> {
  const out = { ...DEFAULT_SETTINGS.optionsShortTargets };
  if (value && typeof value === "object") {
    for (const group of Object.keys(out) as ShortTargetGroup[]) {
      const t = (value as Record<string, unknown>)[group] as Partial<ShortTarget> | undefined;
      if (t && (t.mode === "delta" || t.mode === "offset") && typeof t.value === "number" && Number.isFinite(t.value)) {
        out[group] = clampShortTarget({ mode: t.mode, value: t.value });
      }
    }
  }
  return out;
}

export const TICKET_MIN_WIDTH = 280;
export const TICKET_MAX_WIDTH = 1000;

export const RISK_CHART_MIN_HEIGHT = 120;
export const RISK_CHART_MAX_HEIGHT = 800;

const STORAGE_KEY = "app:settings";
const VERSION = 1;

const THEME_IDS: ChartThemeId[] = ["classic", "tradingview", "monochrome", "colorblind", "muted", "custom"];

function parseCustomColors(value: unknown): CustomColors {
  const out = { ...DEFAULT_CUSTOM_COLORS };
  if (value && typeof value === "object") {
    for (const key of Object.keys(out) as (keyof CustomColors)[]) {
      const candidate = (value as Record<string, unknown>)[key];
      if (isHexColor(candidate)) out[key] = candidate;
    }
  }
  return out;
}

type Listener = (settings: AppSettings) => void;
const listeners = new Set<Listener>();

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

function load(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<AppSettings> & { version?: number };
    // Field by field with the defaults filled in, so an older or foreign
    // value never leaves a setting undefined.
    return {
      chartTheme: oneOf(parsed.chartTheme, THEME_IDS, DEFAULT_SETTINGS.chartTheme),
      candleStyle: oneOf(parsed.candleStyle, ["filled", "hollow"] as const, DEFAULT_SETTINGS.candleStyle),
      sessionShading: typeof parsed.sessionShading === "boolean" ? parsed.sessionShading : DEFAULT_SETTINGS.sessionShading,
      colorMode: oneOf(parsed.colorMode, ["system", "light", "dark"] as const, DEFAULT_SETTINGS.colorMode),
      defaultTimeframe: typeof parsed.defaultTimeframe === "string" ? parsed.defaultTimeframe : DEFAULT_SETTINGS.defaultTimeframe,
      defaultChartType: oneOf(parsed.defaultChartType, ["candles", "line"] as const, DEFAULT_SETTINGS.defaultChartType),
      autoScroll: typeof parsed.autoScroll === "boolean" ? parsed.autoScroll : DEFAULT_SETTINGS.autoScroll,
      vwapAnchor: oneOf(parsed.vwapAnchor, ["session", "premarket"] as const, DEFAULT_SETTINGS.vwapAnchor),
      numberFormat: oneOf(parsed.numberFormat, ["auto", "point", "comma"] as const, DEFAULT_SETTINGS.numberFormat),
      riskChartHeight:
        typeof parsed.riskChartHeight === "number" && Number.isFinite(parsed.riskChartHeight)
          ? Math.min(RISK_CHART_MAX_HEIGHT, Math.max(RISK_CHART_MIN_HEIGHT, Math.round(parsed.riskChartHeight)))
          : DEFAULT_SETTINGS.riskChartHeight,
      optionsTicketWidth:
        typeof parsed.optionsTicketWidth === "number" && Number.isFinite(parsed.optionsTicketWidth)
          ? Math.min(TICKET_MAX_WIDTH, Math.max(TICKET_MIN_WIDTH, Math.round(parsed.optionsTicketWidth)))
          : DEFAULT_SETTINGS.optionsTicketWidth,
      customColors: parseCustomColors(parsed.customColors),
      optionsShortTargets: parseShortTargets(parsed.optionsShortTargets),
      optionsLimitMode: oneOf(parsed.optionsLimitMode, ["mid", "natural"] as const, DEFAULT_SETTINGS.optionsLimitMode),
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

let settings: AppSettings = load();

const darkQuery = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

export function getSettings(): AppSettings {
  return settings;
}

/** Whether the page is dark right now: the override, else the OS. */
export function isDark(): boolean {
  if (settings.colorMode === "dark") return true;
  if (settings.colorMode === "light") return false;
  return darkQuery?.matches ?? false;
}

export function getPalette(): ChartPalette {
  if (settings.chartTheme === "custom") return customPalette(settings.customColors);
  const theme = chartTheme(settings.chartTheme);
  return isDark() ? theme.dark : theme.light;
}

/** The custom colours as a palette regardless of the selected scheme --
 * the Custom tile's preview. */
export function getCustomPalette(): ChartPalette {
  return customPalette(settings.customColors);
}

/** Whether rising candles draw hollow: the style setting, or the theme's
 * own rule (monochrome). */
export function hollowCandles(): boolean {
  return settings.candleStyle === "hollow" || getPalette().forceHollow === true;
}

/** The locale numbers are formatted in; undefined = the browser's. */
export function numberLocale(): string | undefined {
  if (settings.numberFormat === "point") return "en-US";
  if (settings.numberFormat === "comma") return "de-DE";
  return undefined;
}

/** Writes the colour mode and the palette into the document: the theme
 * attribute styles.css keys its variables on, and the CSS variables the
 * tables, the risk chart and the delta classes read. */
export function applyDocumentTheme(): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (settings.colorMode === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", settings.colorMode);
  const p = getPalette();
  root.style.setProperty("--chart-up", p.up);
  root.style.setProperty("--chart-down", p.down);
  root.style.setProperty("--chart-up-soft", p.upSoft);
  root.style.setProperty("--chart-down-soft", p.downSoft);
  root.style.setProperty("--delta-up", p.deltaUp);
  root.style.setProperty("--delta-down", p.deltaDown);
}

function notify(): void {
  applyDocumentTheme();
  listeners.forEach((fn) => fn(settings));
}

export function updateSettings(patch: Partial<AppSettings>): void {
  const next = { ...settings, ...patch };
  const changed = (Object.keys(next) as (keyof AppSettings)[]).some((k) => next[k] !== settings[k]);
  if (!changed) return;
  settings = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: VERSION, ...settings }));
  } catch {
    // Works for this session, just not remembered next time.
  }
  notify();
}

export function resetSettings(): void {
  updateSettings({ ...DEFAULT_SETTINGS });
}

export function subscribeSettings(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// "System" follows a live OS change: the palette variant and the dockview
// theme re-derive from isDark(), so subscribers re-render.
darkQuery?.addEventListener("change", () => {
  if (settings.colorMode === "system") notify();
});

applyDocumentTheme();
