import { useState } from "react";

import { LIVE_CONFIRMATION, type TradingMode } from "../../api/tradingMode";
import { useTradingContext } from "../../context/TradingContext";
import { Modal } from "../common/Modal";

interface TradingModeSwitchProps {
  mode: TradingMode;
  onChange: (mode: TradingMode) => void;
}

/** Simulation / Paper / Live in the header. Live is only offered when the
 * backend reports a configured live account *and* TRADING_ALLOW_LIVE, and
 * even then entering it goes through a typed confirmation -- the same word
 * every live order asks for again. Leaving Live never asks: getting out of
 * the real account must always be one click. */
export function TradingModeSwitch({ mode, onChange }: TradingModeSwitchProps) {
  const { liveAvailable, liveAllowed } = useTradingContext();
  const [confirmingLive, setConfirmingLive] = useState(false);
  const [typed, setTyped] = useState("");

  const liveOffered = liveAvailable && liveAllowed;
  const liveTitle = !liveAvailable
    ? "No live account configured (ALPACA_LIVE_API_KEY_ID / _SECRET_KEY in backend/.env)."
    : !liveAllowed
      ? "Live trading is switched off (TRADING_ALLOW_LIVE in backend/.env)."
      : "Real money. Every order, cancel and close will ask you to type LIVE.";

  const pick = (next: TradingMode) => {
    if (next === mode) return;
    if (next === "live") {
      setTyped("");
      setConfirmingLive(true);
      return;
    }
    onChange(next);
  };

  return (
    <>
      <div className="trading-mode-switch" role="group" aria-label="Trading account">
        <button
          type="button"
          className="trading-mode-toggle simulation"
          aria-pressed={mode === "simulation"}
          onClick={() => pick("simulation")}
          title="Simulation: orders fill against live prices in a local, practice-only book -- never a real account."
        >
          Simulation
        </button>
        <button
          type="button"
          className="trading-mode-toggle paper"
          aria-pressed={mode === "paper"}
          onClick={() => pick("paper")}
          title="The Alpaca paper account."
        >
          Paper
        </button>
        <button
          type="button"
          className="trading-mode-toggle live"
          aria-pressed={mode === "live"}
          disabled={!liveOffered}
          onClick={() => pick("live")}
          title={liveTitle}
        >
          Live
        </button>
      </div>
      <Modal
        open={confirmingLive}
        title="Switch to the real-money account"
        onClose={() => setConfirmingLive(false)}
      >
        <div className="order-confirm">
          <p className="order-confirm-line">
            Positions, orders and the ticket will show the <strong>real Alpaca account</strong>.
            Every order, cancel, stop move and close in Live mode asks for this word again, and
            instant-fire buttons and hotkeys are off.
          </p>
          <p className="order-confirm-mode">LIVE — real money</p>
          <label className="order-confirm-line">
            Type {LIVE_CONFIRMATION} to switch{" "}
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          </label>
          <div className="order-confirm-actions">
            <button type="button" className="timeframe-button" onClick={() => setConfirmingLive(false)}>
              Stay on {mode === "simulation" ? "Simulation" : "Paper"}
            </button>
            <button
              type="button"
              className="generate-button live-action"
              disabled={typed.trim() !== LIVE_CONFIRMATION}
              onClick={() => {
                setConfirmingLive(false);
                onChange("live");
              }}
            >
              Switch to Live
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}
