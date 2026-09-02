import { LIVE_CONFIRMATION, type TradingMode } from "../../api/tradingMode";

interface LiveConfirmFieldProps {
  mode: TradingMode;
  value: string;
  onChange: (value: string) => void;
}

/** The typed-confirmation line inside a confirm dialog. Renders nothing
 * outside Live mode, so every dialog can include it unconditionally and
 * gate its action button on liveConfirmed(mode, value). The value is also
 * what goes out as the X-Live-Confirm header: the backend refuses a live
 * write without it (app/trading/guards.py). */
export function LiveConfirmField({ mode, value, onChange }: LiveConfirmFieldProps) {
  if (mode !== "live") return null;
  return (
    <label className="order-confirm-line live-confirm-field">
      Type {LIVE_CONFIRMATION} to confirm{" "}
      <input
        type="text"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
