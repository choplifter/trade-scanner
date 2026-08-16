import type { LayoutMode } from "../../hooks/useDashboardLayout";

interface LayoutModeToggleProps {
  mode: LayoutMode;
  onChange: (mode: LayoutMode) => void;
  onReset: () => void;
}

/**
 * Switches the dashboard between the fixed splitter layout and the
 * draggable grid, alongside the alarms toggle in the header. Grid mode also
 * exposes a reset, since it's possible to drag yourself into an arrangement
 * that's easier to start over from than to fix by hand.
 */
export function LayoutModeToggle({ mode, onChange, onReset }: LayoutModeToggleProps) {
  const grid = mode === "grid";
  return (
    <span className="layout-mode-group">
      {grid && (
        <button
          type="button"
          className="layout-reset-button"
          onClick={onReset}
          title="Restore the default widget arrangement"
        >
          Reset
        </button>
      )}
      <button
        type="button"
        className="layout-mode-toggle"
        aria-pressed={grid}
        onClick={() => onChange(grid ? "panels" : "grid")}
        title={
          grid
            ? "Grid layout -- drag a widget by its header to move it, drag its bottom-right corner to resize"
            : "Panel layout -- drag the splitters to resize; switch to Grid to reposition widgets freely"
        }
      >
        Layout: {grid ? "Grid" : "Panels"}
      </button>
    </span>
  );
}
