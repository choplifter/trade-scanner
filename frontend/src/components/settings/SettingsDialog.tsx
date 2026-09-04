import { useEffect, useState } from "react";

import { CHART_THEMES, type ChartThemeId } from "../../api/chartTheme";
import { isDark, resetSettings, type AppSettings } from "../../api/settings";
import type { SettingsTab } from "../../api/settingsDialog";
import { useSettings } from "../../hooks/useSettings";
import { TIMEFRAME_OPTIONS } from "../../utils/aggregateBars";
import { Modal } from "../common/Modal";
import { BrokerTab } from "./BrokerTab";
import { HOTKEY_GROUPS } from "./hotkeys";

type Tab = SettingsTab;

const TABS: { key: Tab; label: string }[] = [
  { key: "appearance", label: "Appearance" },
  { key: "chart", label: "Chart" },
  { key: "display", label: "Display" },
  { key: "broker", label: "Broker" },
  { key: "hotkeys", label: "Hotkeys" },
];

interface SettingsDialogProps {
  open: boolean;
  onClose: () => void;
  /** The tab to show when opened by a widget (see api/settingsDialog.ts);
   * null keeps whatever was open last. */
  initialTab?: Tab | null;
}

/** Two candles in a theme's colours, for the scheme tiles. */
function ThemeSwatch({ id }: { id: ChartThemeId }) {
  const theme = CHART_THEMES.find((t) => t.id === id)!;
  const p = isDark() ? theme.dark : theme.light;
  const hollow = p.forceHollow === true;
  return (
    <svg className="settings-swatch" viewBox="0 0 40 28" aria-hidden="true">
      <line x1="12" y1="3" x2="12" y2="25" stroke={p.up} strokeWidth="1.5" />
      <rect x="7" y="8" width="10" height="12" fill={hollow ? "transparent" : p.up} stroke={p.up} strokeWidth="1.5" />
      <line x1="28" y1="4" x2="28" y2="26" stroke={p.down} strokeWidth="1.5" />
      <rect x="23" y="9" width="10" height="12" fill={p.down} stroke={p.down} strokeWidth="1.5" />
    </svg>
  );
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { key: T; label: string; title?: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="timeframe-selector">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          className="timeframe-button"
          aria-pressed={value === o.key}
          onClick={() => onChange(o.key)}
          title={o.title}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="settings-row">
      <div className="settings-row-label">
        <span>{label}</span>
        {hint && <small>{hint}</small>}
      </div>
      <div className="settings-row-control">{children}</div>
    </div>
  );
}

const SAMPLE = 1234.56;

/** The Settings dialog. Every control writes straight into the settings
 * store (api/settings.ts); nothing is staged, there is no Save button,
 * and every open chart and table follows at once. */
export function SettingsDialog({ open, onClose, initialTab = null }: SettingsDialogProps) {
  const [settings, update] = useSettings();
  const [tab, setTab] = useState<Tab>("appearance");
  useEffect(() => {
    if (open && initialTab) setTab(initialTab);
  }, [open, initialTab]);
  const set = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => update({ [key]: value } as Partial<AppSettings>);

  return (
    <Modal open={open} title="Settings" onClose={onClose} className="modal-panel-wide">
      <div className="settings-dialog">
        <div className="timeframe-selector settings-tabs">
          {TABS.map((t) => (
            <button key={t.key} type="button" className="timeframe-button" aria-pressed={tab === t.key} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>

        {tab === "appearance" && (
          <div className="settings-section">
            <Row label="Chart colour scheme" hint="Candles, wicks, volume, position lines, the risk chart and the tables' up/down colours.">
              <div className="settings-themes">
                {CHART_THEMES.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className="settings-theme"
                    aria-pressed={settings.chartTheme === t.id}
                    onClick={() => set("chartTheme", t.id)}
                    title={t.description}
                  >
                    <ThemeSwatch id={t.id} />
                    <span>{t.label}</span>
                  </button>
                ))}
              </div>
            </Row>
            <Row label="Candle style" hint="Monochrome always draws rising candles hollow.">
              <Segmented
                value={settings.candleStyle}
                options={[
                  { key: "filled", label: "Filled" },
                  { key: "hollow", label: "Hollow up" },
                ]}
                onChange={(v) => set("candleStyle", v)}
              />
            </Row>
            <Row label="Light / dark" hint="System follows the operating system, live.">
              <Segmented
                value={settings.colorMode}
                options={[
                  { key: "system", label: "System" },
                  { key: "light", label: "Light" },
                  { key: "dark", label: "Dark" },
                ]}
                onChange={(v) => set("colorMode", v)}
              />
            </Row>
            <Row label="Session shading" hint="Tint premarket and after-hours bars on intraday charts.">
              <Segmented
                value={settings.sessionShading ? "on" : "off"}
                options={[
                  { key: "on", label: "On" },
                  { key: "off", label: "Off" },
                ]}
                onChange={(v) => set("sessionShading", v === "on")}
              />
            </Row>
          </div>
        )}

        {tab === "chart" && (
          <div className="settings-section">
            <p className="order-hint">Defaults for a chart when it loads; the buttons in the chart still change only that chart.</p>
            <Row label="Timeframe">
              <Segmented
                value={settings.defaultTimeframe}
                options={TIMEFRAME_OPTIONS.map((o) => ({ key: o.key, label: o.label }))}
                onChange={(v) => set("defaultTimeframe", v)}
              />
            </Row>
            <Row label="Chart type">
              <Segmented
                value={settings.defaultChartType}
                options={[
                  { key: "candles", label: "Candles" },
                  { key: "line", label: "Line" },
                ]}
                onChange={(v) => set("defaultChartType", v)}
              />
            </Row>
            <Row label="Auto-scroll" hint="Follow the newest candle (TradingView's auto-scroll).">
              <Segmented
                value={settings.autoScroll ? "on" : "off"}
                options={[
                  { key: "on", label: "On" },
                  { key: "off", label: "Off" },
                ]}
                onChange={(v) => set("autoScroll", v === "on")}
              />
            </Row>
            <Row label="VWAP anchor" hint="Session = 09:30 open; premarket = every print since the premarket open (TradingView's).">
              <Segmented
                value={settings.vwapAnchor}
                options={[
                  { key: "session", label: "Session" },
                  { key: "premarket", label: "Premarket" },
                ]}
                onChange={(v) => set("vwapAnchor", v)}
              />
            </Row>
          </div>
        )}

        {tab === "display" && (
          <div className="settings-section">
            <Row label="Number format" hint="Money and quantities across the app, and the chart axes. Price inputs always use a point.">
              <Segmented
                value={settings.numberFormat}
                options={[
                  { key: "auto", label: `Browser (${SAMPLE.toLocaleString()})` },
                  { key: "point", label: SAMPLE.toLocaleString("en-US") },
                  { key: "comma", label: SAMPLE.toLocaleString("de-DE") },
                ]}
                onChange={(v) => set("numberFormat", v)}
              />
            </Row>
          </div>
        )}

        {tab === "broker" && <BrokerTab />}

        {tab === "hotkeys" && (
          <div className="settings-section settings-hotkeys">
            {HOTKEY_GROUPS.map((group) => (
              <div key={group.title} className="settings-hotkey-group">
                <h3>{group.title}</h3>
                {group.note && <p className="order-hint">{group.note}</p>}
                <table className="performance-table">
                  <tbody>
                    {group.keys.map((k) => (
                      <tr key={k.keys}>
                        <td className="settings-hotkey-keys">
                          <kbd>{k.keys}</kbd>
                        </td>
                        <td>{k.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}

        <div className="settings-footer">
          <button type="button" className="row-action" onClick={resetSettings} title="Back to the defaults for every setting">
            Reset to defaults
          </button>
          <span className="order-hint">Changes apply at once and are remembered in this browser.</span>
        </div>
      </div>
    </Modal>
  );
}
