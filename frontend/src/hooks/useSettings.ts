import { useEffect, useState } from "react";

import type { ChartPalette } from "../api/chartTheme";
import {
  getPalette,
  getSettings,
  hollowCandles,
  isDark,
  subscribeSettings,
  updateSettings,
  type AppSettings,
} from "../api/settings";

/** The current settings and a patch function; re-renders on any change
 * (including an OS colour-scheme change while in "system" mode). */
export function useSettings(): [AppSettings, (patch: Partial<AppSettings>) => void] {
  const [settings, setState] = useState<AppSettings>(getSettings);
  useEffect(() => subscribeSettings(setState), []);
  return [settings, updateSettings];
}

/** The chart palette in force (theme x light/dark) plus the candle style,
 * as one memo-friendly object that changes identity only when the values
 * do. */
export function useChartPalette(): { palette: ChartPalette; hollow: boolean; dark: boolean } {
  const [snapshot, setSnapshot] = useState(() => ({ palette: getPalette(), hollow: hollowCandles(), dark: isDark() }));
  useEffect(
    () =>
      subscribeSettings(() => {
        setSnapshot((current) => {
          const next = { palette: getPalette(), hollow: hollowCandles(), dark: isDark() };
          return next.palette === current.palette && next.hollow === current.hollow && next.dark === current.dark
            ? current
            : next;
        });
      }),
    [],
  );
  return snapshot;
}

export function useEffectiveDark(): boolean {
  return useChartPalette().dark;
}
