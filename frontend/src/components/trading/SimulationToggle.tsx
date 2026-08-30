import type { TradingMode } from "../../api/tradingMode";

interface SimulationToggleProps {
  mode: TradingMode;
  onChange: (mode: TradingMode) => void;
}

export function SimulationToggle({ mode, onChange }: SimulationToggleProps) {
  const enabled = mode === "simulation";
  return (
    <button
      type="button"
      className="simulation-toggle"
      aria-pressed={enabled}
      onClick={() => onChange(enabled ? "live" : "simulation")}
      title="Simulation Mode: orders fill against real live prices in a fully local, practice-only account -- never the real paper account."
    >
      Simulation {enabled ? "On" : "Off"}
    </button>
  );
}
