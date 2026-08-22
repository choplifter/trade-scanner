interface AlarmsToggleProps {
  enabled: boolean;
  onToggle: (value: boolean) => void;
  activeCount: number;
  onClickCount: () => void;
}

export function AlarmsToggle({ enabled, onToggle, activeCount, onClickCount }: AlarmsToggleProps) {
  return (
    <span className="alarms-toggle-group">
      {enabled && activeCount > 0 && (
        <button
          type="button"
          className="alarms-count-badge"
          onClick={onClickCount}
          title="Show active momentum alarms"
        >
          🔔 {activeCount}
        </button>
      )}
      <button
        type="button"
        className="alarms-toggle"
        aria-pressed={enabled}
        onClick={() => onToggle(!enabled)}
        title="Alarm on a fast, wick-less move -- a large price change over the momentum window with almost no pullback candle"
      >
        Alarms {enabled ? "On" : "Off"}
      </button>
    </span>
  );
}
