import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

/** What the Options widget wants drawn on the chart for the selected
 * symbol: the strikes of the spread being built or held, and any armed
 * underlying stop/target. Kept in its own context, like IndicativeLevels
 * in useTrading, so nothing that changes per poll tick has to pass through
 * App.tsx's widgets memo (which would remount the chart). */
export interface SpreadLevels {
  symbol: string;
  strikes: { label: string; price: number; role: "long" | "short" }[];
  closeBelow: number | null;
  closeAbove: number | null;
}

interface SpreadLevelsValue {
  levels: SpreadLevels | null;
  setLevels: (levels: SpreadLevels | null) => void;
}

const SpreadLevelsContext = createContext<SpreadLevelsValue | null>(null);

export function SpreadLevelsProvider({ children }: { children: ReactNode }) {
  const [levels, setLevels] = useState<SpreadLevels | null>(null);
  const value = useMemo(() => ({ levels, setLevels }), [levels]);
  return <SpreadLevelsContext.Provider value={value}>{children}</SpreadLevelsContext.Provider>;
}

export function useSpreadLevelsContext(): SpreadLevelsValue {
  const value = useContext(SpreadLevelsContext);
  if (!value) throw new Error("useSpreadLevelsContext must be used within a SpreadLevelsProvider");
  return value;
}
