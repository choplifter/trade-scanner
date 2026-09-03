import type { LayoutMode } from "../../hooks/useDashboardLayout";

interface LayoutModeToggleProps {
  mode: LayoutMode;
  onChange: (mode: LayoutMode) => void;
  onReset: () => void;
}

const MODES: { key: LayoutMode; label: string; title: string }[] = [
  {
    key: "panels",
    label: "Panels",
    title: "Panel layout -- drag the splitters to resize; switch modes to reposition widgets freely",
  },
  {
    key: "grid",
    label: "Grid",
    title: "Grid layout -- drag a widget by its header to move it, drag its bottom-right corner to resize",
  },
  {
    key: "dock",
    label: "Dock",
    title:
      "Dock layout (prototype) -- drag a tab to rearrange or dock it elsewhere, drag it off the layout to float it, use its × to close. Maximize/reopen-a-closed-widget aren't wired up to any button yet in this pass.",
  },
];

/**
 * Switches the dashboard between the fixed splitter layout, the draggable
 * grid, and the docking-layout prototype (DockviewDashboard), alongside the
 * alarms toggle in the header. Grid mode also exposes a reset, since it's
 * possible to drag yourself into an arrangement that's easier to start over
 * from than to fix by hand -- dock mode has no persisted arrangement to
 * reset (see LayoutMode's docs), so it doesn't get one.
 */
export function LayoutModeToggle({ mode, onChange, onReset }: LayoutModeToggleProps) {
  return (
    <span className="layout-mode-group">
      {(mode === "grid" || mode === "dock") && (
        <button
          type="button"
          className="layout-reset-button"
          onClick={onReset}
          title={
            mode === "dock"
              ? "Restore the default dock arrangement (closes widget copies and floating windows)"
              : "Restore the default widget arrangement"
          }
        >
          Reset
        </button>
      )}
      {MODES.map((m) => (
        <button
          key={m.key}
          type="button"
          className="layout-mode-toggle"
          aria-pressed={mode === m.key}
          onClick={() => onChange(m.key)}
          title={m.title}
        >
          {m.label}
        </button>
      ))}
    </span>
  );
}
