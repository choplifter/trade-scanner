/** Two small app-wide signals, module singletons like api/tradingMode.ts:
 *
 * - openSettings(tab): any widget can open the Settings dialog on a tab
 *   (the "connect your broker" panels do), without threading a callback
 *   through the dock. App.tsx subscribes.
 * - brokerChanged(): fired after a broker key pair was connected or
 *   removed, so the trading and options hooks refetch at once instead of
 *   waiting for their next poll.
 */

export type SettingsTab = "appearance" | "chart" | "display" | "broker" | "hotkeys";

type OpenListener = (tab: SettingsTab | null) => void;
const openListeners = new Set<OpenListener>();

export function openSettings(tab: SettingsTab | null = null): void {
  openListeners.forEach((fn) => fn(tab));
}

export function subscribeOpenSettings(fn: OpenListener): () => void {
  openListeners.add(fn);
  return () => openListeners.delete(fn);
}

type BrokerListener = () => void;
const brokerListeners = new Set<BrokerListener>();

export function brokerChanged(): void {
  brokerListeners.forEach((fn) => fn());
}

export function subscribeBrokerChanged(fn: BrokerListener): () => void {
  brokerListeners.add(fn);
  return () => brokerListeners.delete(fn);
}
