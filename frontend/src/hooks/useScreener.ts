import { useCallback, useEffect, useState } from "react";

import { getScreenerFields, getScreenerPresets, runScreen } from "../api/http";
import type { FieldSpec, Preset, Screen, ScreenResponse } from "../types/screener";

export interface ScreenerState {
  /** The server's field registry. Empty until loaded; every picker in the
   * UI derives from it, so the UI renders disabled rather than guessing. */
  fields: FieldSpec[];
  presets: Preset[];
  result: ScreenResponse | null;
  loading: boolean;
  error: string | null;
  run: (screen: Screen) => void;
}

/**
 * Loads the field registry and presets once, then runs screens on demand.
 *
 * Deliberately request-driven rather than subscribed to the scanner
 * WebSocket: a screen is a question about the whole universe, and the WS
 * topics only ever carry one ranked view's top rows (see scanner_ws). Live
 * re-running on every poll tick would also fight the user mid-edit, so a
 * screen runs when asked.
 */
export function useScreener(): ScreenerState {
  const [fields, setFields] = useState<FieldSpec[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [result, setResult] = useState<ScreenResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getScreenerFields(), getScreenerPresets()])
      .then(([fieldsRes, presetsRes]) => {
        if (cancelled) return;
        setFields(fieldsRes.fields);
        setPresets(presetsRes.presets);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load screener fields");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const run = useCallback((screen: Screen) => {
    setLoading(true);
    setError(null);
    runScreen(screen)
      .then((res) => {
        setResult(res);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Screen failed");
        setLoading(false);
      });
  }, []);

  return { fields, presets, result, loading, error, run };
}
