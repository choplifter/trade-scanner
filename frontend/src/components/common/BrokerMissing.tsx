import { openSettings } from "../../api/settingsDialog";
import type { TradingMode } from "../../api/tradingMode";

/** What a trading surface shows instead of an error when this login has
 * no Alpaca key pair for the account it is looking at (backend
 * "broker_not_connected"): one sentence and the way to Settings → Broker. */
export function BrokerMissing({ mode, compact = false }: { mode: TradingMode; compact?: boolean }) {
  const account = mode === "live" ? "real-money" : "paper";
  return (
    <div className={`broker-missing${compact ? " compact" : ""}`}>
      <span>
        No Alpaca {account} account connected for your login. Enter your API keys to trade here, or switch to
        Simulation.
      </span>
      <button type="button" className="generate-button" onClick={() => openSettings("broker")}>
        Connect broker
      </button>
    </div>
  );
}
