import { useEffect, useMemo, useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { runPlaybookBacktest } from "../../api/playbooks";
import type { TradingMode } from "../../api/tradingMode";
import type { CampaignsActions, CampaignsState } from "../../hooks/useCampaigns";
import type { LoadableStructure, SpreadGroup } from "../../types/options";
import type { BacktestResult, Campaign, CampaignEvent, ParamSpec, PlaybookScript, Proposal } from "../../types/playbooks";
import { formatMoney } from "../../utils/format";
import { formatExpiry, formatLeg } from "../../utils/occ";
import { formatDateTime } from "../../utils/time";
import type { PlaybookIntent } from "./playbookIntent";
import type { RollTarget } from "./RollTicket";

interface PlaybooksTabProps {
  symbol: string | null;
  mode: TradingMode;
  campaigns: CampaignsState & CampaignsActions;
  /** The account's open spreads, to hand a roll proposal its held group. */
  spreads: SpreadGroup[];
  intent: PlaybookIntent | null;
  onIntentHandled: (seq: number) => void;
  onLoad: (structure: LoadableStructure) => boolean;
  onSelectSymbol?: (symbol: string) => void;
  onRoll: (target: RollTarget) => void;
}

const PHASE_LABEL: Record<string, string> = {
  cash: "Cash",
  short_put: "Short put",
  assigned: "Assigned",
  covered_call: "Covered call",
  mixed: "Mixed",
};

const EVENT_LABEL: Record<string, string> = {
  started: "Started",
  sold_put: "Sold put",
  sold_call: "Sold call",
  closed: "Closed",
  rolled: "Rolled",
  expired: "Expired",
  cash_settled: "Cash settled",
  assigned: "Assigned",
  called_away: "Called away",
  shares_changed: "Shares changed",
  manual_note: "Note",
  paused: "Paused",
  resumed: "Resumed",
  campaign_closed: "Campaign closed",
  executed: "Executed",
  execute_failed: "Execute failed",
};

function errorText(err: unknown): string {
  return err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err);
}

function eventLine(e: CampaignEvent): string {
  const parts: string[] = [];
  if (e.occ) parts.push(formatLeg(e.occ));
  if (e.kind === "assigned" || e.kind === "called_away" || e.kind === "shares_changed") {
    parts.push(`${e.qty > 0 ? "+" : ""}${e.qty} shares${e.price != null ? ` at ${e.price.toFixed(2)}` : ""}`);
  } else if (e.price != null && e.kind !== "started") {
    parts.push(`${e.qty}× at ${e.price.toFixed(2)}`);
  }
  if (e.cash_delta) parts.push(`${e.cash_delta > 0 ? "+" : ""}${formatMoney(e.cash_delta)}`);
  if (e.note) parts.push(e.note);
  return parts.join(" · ");
}

/** The start form: one field per ParamSpec, typed by the spec. */
function ParamsForm({ script, values, onChange }: { script: PlaybookScript; values: Record<string, number | boolean>; onChange: (v: Record<string, number | boolean>) => void }) {
  const set = (spec: ParamSpec, raw: string | boolean) => {
    if (spec.type === "bool") {
      onChange({ ...values, [spec.name]: Boolean(raw) });
      return;
    }
    const n = Number(raw);
    onChange({ ...values, [spec.name]: Number.isFinite(n) ? n : (spec.default as number) });
  };
  return (
    <div className="pb-params">
      {script.params.map((spec) => (
        <label key={spec.name} className={`pb-param${spec.type === "bool" ? " check" : ""}`} title={spec.help}>
          {spec.type === "bool" ? (
            <>
              <input type="checkbox" checked={Boolean(values[spec.name] ?? spec.default)} onChange={(e) => set(spec, e.target.checked)} /> {spec.label}
            </>
          ) : (
            <>
              {spec.label}
              <input
                type="number"
                value={String(values[spec.name] ?? spec.default)}
                min={spec.min ?? undefined}
                max={spec.max ?? undefined}
                step={spec.step ?? (spec.type === "int" ? 1 : 0.01)}
                onChange={(e) => set(spec, e.target.value)}
              />
            </>
          )}
        </label>
      ))}
    </div>
  );
}

function ProposalCard({
  campaign,
  proposal,
  symbol,
  spreads,
  onLoad,
  onSelectSymbol,
  onRoll,
  onRecompute,
  busy,
}: {
  campaign: Campaign;
  proposal: Proposal;
  symbol: string | null;
  spreads: SpreadGroup[];
  onLoad: (structure: LoadableStructure) => boolean;
  onSelectSymbol?: (symbol: string) => void;
  onRoll: (target: RollTarget) => void;
  onRecompute: () => void;
  busy: boolean;
}) {
  const [note, setNote] = useState<string | null>(null);
  const onSymbol = symbol === campaign.symbol;
  const load = () => {
    if (!proposal.ticket) return;
    if (!onSymbol) {
      onSelectSymbol?.(campaign.symbol);
      setNote(`${campaign.symbol} selected; click again once its chain is in.`);
      return;
    }
    const ok = onLoad({ strategy: proposal.ticket.strategy, ticket: proposal.ticket });
    setNote(ok ? null : "Could not load into the ticket (is that expiry listed?).");
  };
  const roll = () => {
    if (!proposal.roll) return;
    const occ = proposal.roll.close.legs[0]?.symbol;
    const group = spreads.find((g) => g.legs.some((l) => l.symbol === occ));
    if (!group) {
      setNote("The leg to roll is not among the open spreads yet.");
      return;
    }
    onRoll({ group, presetExpiry: proposal.roll.open.expiry, presetStrike: proposal.roll.open.legs?.[0]?.strike });
  };
  return (
    <div className={`pb-proposal ${proposal.kind}`}>
      <div className="pb-proposal-head">
        <span className="pb-proposal-kind">{proposal.kind === "hold" ? "Hold" : "Next step"}</span>
        <strong>{proposal.sentence}</strong>
      </div>
      <p className="pb-proposal-reason">{proposal.reason}</p>
      <p className="order-hint">
        computed {formatDateTime(proposal.computed_at)} · spot {proposal.spot.toFixed(2)}
        {proposal.expiries_loaded.length ? ` · chains ${proposal.expiries_loaded.map((e) => formatExpiry(e)).join(", ")}` : ""}
      </p>
      <div className="pb-proposal-actions">
        {proposal.ticket && (
          <button type="button" className="generate-button" onClick={load} title={onSymbol ? "Prefill the ticket on the Chain tab" : `Select ${campaign.symbol} first`}>
            {onSymbol ? "Load into ticket" : `Select ${campaign.symbol}`}
          </button>
        )}
        {proposal.roll && (
          <button type="button" className="generate-button" onClick={roll}>
            Open roll ticket
          </button>
        )}
        <button type="button" className="row-action" onClick={onRecompute} disabled={busy}>
          {busy ? "Computing…" : "Recompute"}
        </button>
      </div>
      {note && <p className="order-hint">{note}</p>}
    </div>
  );
}

function CampaignCard({
  campaign,
  symbol,
  mode,
  spreads,
  actions,
  onLoad,
  onSelectSymbol,
  onRoll,
}: {
  campaign: Campaign;
  symbol: string | null;
  mode: TradingMode;
  spreads: SpreadGroup[];
  actions: CampaignsActions;
  onLoad: (structure: LoadableStructure) => boolean;
  onSelectSymbol?: (symbol: string) => void;
  onRoll: (target: RollTarget) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [armAuto, setArmAuto] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [showAll, setShowAll] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const events = [...campaign.events].reverse();
  const shown = showAll ? events : events.slice(0, 6);
  return (
    <li className={`pb-campaign ${campaign.status}${symbol === campaign.symbol ? " selected" : ""}`}>
      <div className="pb-campaign-head">
        <button type="button" className="pb-symbol" onClick={() => onSelectSymbol?.(campaign.symbol)} title="Select this symbol">
          {campaign.symbol}
        </button>
        <span className={`pb-phase ${campaign.phase}`}>{PHASE_LABEL[campaign.phase] ?? campaign.phase}</span>
        <span className="order-hint">{campaign.playbook}</span>
        {campaign.status !== "active" && <span className="pb-status">{campaign.status}</span>}
        {campaign.auto_execute && <span className="pb-auto" title="The runner places this campaign's proposals itself (Simulation only).">auto</span>}
        <span className="pb-spacer" />
        <span className="order-hint">since {formatDateTime(campaign.created_at)}</span>
      </div>
      <div className="pb-numbers">
        <span title="Shares held in the underlying, with their average entry (an assignment enters at the strike).">
          <strong>{campaign.shares}</strong> shares{campaign.shares_avg_entry != null ? ` @ ${campaign.shares_avg_entry.toFixed(2)}` : ""}
        </span>
        <span title="(average entry × shares − premiums collected) / shares: what the shares must be called away above for the campaign to have made money.">
          basis <strong>{campaign.cost_basis != null ? campaign.cost_basis.toFixed(2) : "—"}</strong>
        </span>
        <span title="Every option credit less every debit since the campaign started.">
          premiums <strong className={campaign.premiums_collected >= 0 ? "delta-up" : "delta-down"}>{formatMoney(campaign.premiums_collected)}</strong>
        </span>
        <span title="Premiums plus the shares' round trips (assigned at the put strike, called away at the call strike).">
          realized <strong className={campaign.realized_pnl >= 0 ? "delta-up" : "delta-down"}>{formatMoney(campaign.realized_pnl)}</strong>
        </span>
      </div>
      {campaign.proposal_error && <p className="order-rejection">Proposal failed: {campaign.proposal_error}</p>}
      {campaign.last_error && <p className="idea-warning">{campaign.last_error}</p>}
      {campaign.proposal && campaign.status === "active" && (
        <ProposalCard
          campaign={campaign}
          proposal={campaign.proposal}
          symbol={symbol}
          spreads={spreads}
          onLoad={onLoad}
          onSelectSymbol={onSelectSymbol}
          onRoll={onRoll}
          onRecompute={() => void run(() => actions.propose(campaign.id))}
          busy={busy}
        />
      )}
      <div className="pb-actions">
        {campaign.status === "active" ? (
          <button type="button" className="row-action" disabled={busy} onClick={() => void run(() => actions.patch(campaign.id, { status: "paused" }))}>
            Pause
          </button>
        ) : campaign.status === "paused" ? (
          <button type="button" className="row-action" disabled={busy} onClick={() => void run(() => actions.patch(campaign.id, { status: "active" }))}>
            Resume
          </button>
        ) : null}
        {campaign.status !== "closed" && (
          <button
            type="button"
            className="row-action"
            disabled={busy}
            title="Ends the campaign's bookkeeping; open positions are left as they are."
            onClick={() => void run(() => actions.patch(campaign.id, { status: "closed" }))}
          >
            Close campaign
          </button>
        )}
        {mode === "simulation" && campaign.status !== "closed" && (
          <label className="pb-auto-toggle" title="Simulation only: the runner places the proposals itself during the regular session. A failure switches it off again.">
            <input
              type="checkbox"
              checked={campaign.auto_execute}
              disabled={busy}
              onChange={(e) => {
                if (!e.target.checked) {
                  setArmAuto(false);
                  void run(() => actions.patch(campaign.id, { auto_execute: false }));
                } else if (!armAuto) {
                  setArmAuto(true);
                } else {
                  setArmAuto(false);
                  void run(() => actions.patch(campaign.id, { auto_execute: true }));
                }
              }}
            />{" "}
            auto-execute{armAuto && !campaign.auto_execute ? " — tick again to confirm" : ""}
          </label>
        )}
      </div>
      {error && <p className="order-rejection">{error}</p>}
      <ul className="pb-events">
        {shown.map((e) => (
          <li key={e.id} className={`pb-event ${e.kind}`}>
            <span className="pb-event-at">{formatDateTime(e.at)}</span>
            <span className="pb-event-kind">{EVENT_LABEL[e.kind] ?? e.kind}</span>
            <span className="pb-event-text">{eventLine(e)}</span>
          </li>
        ))}
      </ul>
      {events.length > 6 && (
        <button type="button" className="row-action" onClick={() => setShowAll((s) => !s)}>
          {showAll ? "Fewer events" : `All ${events.length} events`}
        </button>
      )}
      {campaign.status !== "closed" && (
        <form
          className="pb-note"
          onSubmit={(e) => {
            e.preventDefault();
            const text = noteText.trim();
            if (!text) return;
            void run(async () => {
              await actions.note(campaign.id, text);
              setNoteText("");
            });
          }}
        >
          <input type="text" value={noteText} placeholder="Add a note…" maxLength={500} onChange={(e) => setNoteText(e.target.value)} />
          <button type="submit" className="row-action" disabled={busy || noteText.trim() === ""}>
            Note
          </button>
        </form>
      )}
    </li>
  );
}

/** Equity against buy-and-hold over the walk, as a small SVG line chart. */
function EquityChart({ result }: { result: BacktestResult }) {
  const W = 600;
  const H = 160;
  const PAD = { l: 52, r: 8, t: 8, b: 18 };
  const pts = result.equity;
  if (pts.length < 2) return null;
  const values = pts.flatMap((p) => [p.equity, p.benchmark]);
  const yMin = Math.min(...values);
  const yMax = Math.max(...values);
  const span = yMax - yMin || 1;
  const x = (i: number) => PAD.l + (i / (pts.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((yMax - v) / span) * (H - PAD.t - PAD.b);
  const line = (key: "equity" | "benchmark") => pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const fmt = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(0)}k` : v.toFixed(0));
  const ticks = [0, Math.floor((pts.length - 1) / 2), pts.length - 1];
  return (
    <svg className="pb-bt-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Equity versus buy and hold">
      <line className="pb-bt-axis" x1={PAD.l} x2={W - PAD.r} y1={y(result.summary.starting_cash)} y2={y(result.summary.starting_cash)} />
      <text className="pb-bt-tick" x={PAD.l - 4} y={y(yMax) + 4} textAnchor="end">
        {fmt(yMax)}
      </text>
      <text className="pb-bt-tick" x={PAD.l - 4} y={y(yMin) + 4} textAnchor="end">
        {fmt(yMin)}
      </text>
      <text className="pb-bt-tick" x={PAD.l - 4} y={y(result.summary.starting_cash) + 4} textAnchor="end">
        {fmt(result.summary.starting_cash)}
      </text>
      {ticks.map((i) => (
        <text key={i} className="pb-bt-tick" x={x(i)} y={H - 4} textAnchor={i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"}>
          {formatExpiry(pts[i].date)}
        </text>
      ))}
      <path className="pb-bt-benchmark" d={line("benchmark")} />
      <path className="pb-bt-equity" d={line("equity")} />
    </svg>
  );
}

/** The synthetic backtest: months of daily closes walked with
 * Black-Scholes chains on realized volatility. How the rules behave, not
 * what they earned -- and the panel says so. */
function BacktestPanel({ symbol, script, params }: { symbol: string | null; script: PlaybookScript | null; params: Record<string, number | boolean> }) {
  const [months, setMonths] = useState("12");
  const [ivPremium, setIvPremium] = useState("1.15");
  const [spread, setSpread] = useState("0.02");
  const [cash, setCash] = useState("100000");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [showEvents, setShowEvents] = useState(false);

  const run = async () => {
    if (!symbol || !script) return;
    setBusy(true);
    setError(null);
    try {
      const filled = Object.fromEntries(script.params.map((p) => [p.name, params[p.name] ?? p.default]));
      setResult(
        await runPlaybookBacktest({
          symbol,
          playbook: script.stem,
          params: filled,
          months: Math.max(1, Math.min(24, Math.floor(Number(months)) || 12)),
          iv_premium: Number(ivPremium) || 1.15,
          starting_cash: Number(cash) || 100_000,
          spread_frac: Number(spread) || 0,
        }),
      );
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const s = result?.summary;
  return (
    <div className="pb-backtest">
      <div className="pb-bt-head">
        <button type="button" className="row-action" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "Hide backtest" : "Backtest (synthetic)"}
        </button>
        <span className="order-hint">
          Walk the {script?.name ?? "playbook"} over past daily closes with Black-Scholes chains — mechanics, not earnings.
        </span>
      </div>
      {open && (
        <>
          <div className="pb-bt-form">
            <label>
              Months <input type="number" min={1} max={24} step={1} value={months} onChange={(e) => setMonths(e.target.value)} />
            </label>
            <label title="sigma = realized volatility × this factor: options usually trade above what the stock then realizes.">
              IV premium <input type="number" min={0.5} max={3} step={0.05} value={ivPremium} onChange={(e) => setIvPremium(e.target.value)} />
            </label>
            <label title="Bid/ask as a fraction of the mid; fills at the bid to sell and the ask to buy back.">
              Spread <input type="number" min={0} max={0.2} step={0.01} value={spread} onChange={(e) => setSpread(e.target.value)} />
            </label>
            <label>
              Cash $ <input type="number" min={1000} step={1000} value={cash} onChange={(e) => setCash(e.target.value)} />
            </label>
            <button type="button" className="generate-button" disabled={busy || !symbol || !script} onClick={() => void run()}>
              {busy ? "Walking…" : `Run on ${symbol ?? "—"}`}
            </button>
          </div>
          <p className="order-hint">Uses the parameters in the start form above (or the script's defaults).</p>
          {error && <p className="order-rejection">{error}</p>}
          {result && s && (
            <div className="pb-bt-result">
              <p className="idea-context">
                {result.symbol} · {formatExpiry(result.from)} – {formatExpiry(result.to)} · {result.sessions} sessions · IV premium {result.iv_premium}
              </p>
              <EquityChart result={result} />
              <div className="pb-bt-legend">
                <span className="pb-bt-legend-equity">playbook</span>
                <span className="pb-bt-legend-benchmark">buy &amp; hold</span>
              </div>
              <div className="pb-numbers">
                <span>
                  return <strong className={s.total_return_pct >= 0 ? "delta-up" : "delta-down"}>{s.total_return_pct.toFixed(1)} %</strong>
                </span>
                <span>
                  buy &amp; hold <strong className={s.buy_and_hold_return_pct >= 0 ? "delta-up" : "delta-down"}>{s.buy_and_hold_return_pct.toFixed(1)} %</strong>
                </span>
                <span>
                  premiums <strong>{formatMoney(s.premiums)}</strong>
                </span>
                <span>
                  realized <strong>{formatMoney(s.realized_pnl)}</strong>
                </span>
                <span>
                  max drawdown <strong>{s.max_drawdown_pct.toFixed(1)} %</strong>
                </span>
                <span>
                  in shares <strong>{s.days_in_shares_pct.toFixed(0)} %</strong> of days
                </span>
              </div>
              <div className="pb-numbers">
                <span>
                  puts sold <strong>{s.puts_sold}</strong>
                </span>
                <span>
                  assigned <strong>{s.assignments}</strong>
                </span>
                <span>
                  calls sold <strong>{s.calls_sold}</strong>
                </span>
                <span>
                  called away <strong>{s.called_away}</strong>
                </span>
                <span>
                  rolls <strong>{s.rolls}</strong>
                </span>
                <span>
                  expired <strong>{s.expired}</strong>
                </span>
                {s.shares_at_end > 0 && (
                  <span>
                    still holding <strong>{s.shares_at_end}</strong> shares{s.cost_basis_at_end != null ? ` @ basis ${s.cost_basis_at_end.toFixed(2)}` : ""}
                  </span>
                )}
              </div>
              <button type="button" className="row-action" onClick={() => setShowEvents((v) => !v)}>
                {showEvents ? "Hide events" : `${result.events.length} events`}
              </button>
              {showEvents && (
                <ul className="pb-events">
                  {[...result.events].reverse().map((e, i) => (
                    <li key={`${e.at}-${i}`} className={`pb-event ${e.kind}`}>
                      <span className="pb-event-at">{formatExpiry(e.at.slice(0, 10))}</span>
                      <span className="pb-event-kind">{EVENT_LABEL[e.kind] ?? e.kind}</span>
                      <span className="pb-event-text">
                        {[
                          e.occ ? formatLeg(e.occ) : null,
                          e.kind === "assigned" || e.kind === "called_away" ? `${e.qty > 0 ? "+" : ""}${e.qty} shares at ${e.price?.toFixed(2)}` : e.price != null ? `${e.qty}× at ${e.price.toFixed(2)}` : null,
                          e.cash_delta ? `${e.cash_delta > 0 ? "+" : ""}${formatMoney(e.cash_delta)}` : null,
                          e.note,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="idea-disclaimer">{result.disclaimer}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Campaigns of the playbook scripts (the Wheel first) in the Simulation
 * account: start one on the selected symbol with its parameters, and for
 * each running campaign see its phase, shares, cost basis, premiums and
 * realized P&L, the next step the script proposes (loaded into the ticket
 * or the roll ticket with a click), its history, and the switches. The app
 * proposes; the user places -- unless auto-execute is on in the simulation.
 */
export function PlaybooksTab({ symbol, mode, campaigns, spreads, intent, onIntentHandled, onLoad, onSelectSymbol, onRoll }: PlaybooksTabProps) {
  const [scriptStem, setScriptStem] = useState<string>("wheel");
  const [values, setValues] = useState<Record<string, number | boolean>>({});
  const [autoExecute, setAutoExecute] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const script = campaigns.scripts.find((s) => s.stem === scriptStem) ?? campaigns.scripts[0] ?? null;
  useEffect(() => {
    if (script) {
      setValues((cur) => (Object.keys(cur).length ? cur : Object.fromEntries(script.params.map((p) => [p.name, p.default]))));
    }
  }, [script]);

  // A "Wheel…" click on a share position: open the form for that symbol.
  useEffect(() => {
    if (!intent) return;
    setFormOpen(true);
    setScriptStem(intent.playbook);
    onIntentHandled(intent.seq);
  }, [intent, onIntentHandled]);

  const running = campaigns.campaigns.find((c) => c.symbol === symbol && c.status !== "closed") ?? null;
  const ordered = useMemo(
    () => [...campaigns.campaigns].sort((a, b) => Number(b.symbol === symbol) - Number(a.symbol === symbol) || b.created_at.localeCompare(a.created_at)),
    [campaigns.campaigns, symbol],
  );

  const start = async () => {
    if (!symbol || !script) return;
    setStarting(true);
    setStartError(null);
    try {
      await campaigns.create({ account: "sim", symbol, playbook: script.stem, params: values, auto_execute: autoExecute });
      setFormOpen(false);
      setAutoExecute(false);
    } catch (err: unknown) {
      setStartError(errorText(err));
    } finally {
      setStarting(false);
    }
  };

  if (mode !== "simulation") {
    return (
      <div className="widget-empty">
        Playbooks run in the Simulation account for now: switch the trading mode to Simulation to start or follow a
        campaign.
      </div>
    );
  }

  return (
    <div className="idea-tab pb-tab">
      <div className="pb-start">
        {symbol ? (
          running ? (
            <p className="order-hint">
              A {running.playbook} campaign is running on {symbol} (below). Close it before starting another.
            </p>
          ) : (
            <>
              <div className="pb-start-head">
                <button type="button" className="generate-button" onClick={() => setFormOpen((o) => !o)} aria-expanded={formOpen}>
                  {formOpen ? "Cancel" : `Start ${script?.name ?? "playbook"} on ${symbol}`}
                </button>
                {campaigns.scripts.length > 1 && (
                  <select value={script?.stem ?? ""} onChange={(e) => setScriptStem(e.target.value)}>
                    {campaigns.scripts.map((s) => (
                      <option key={s.stem} value={s.stem}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                )}
                {campaigns.scriptErrors.map((e) => (
                  <span key={e.filename} className="order-rejection" title={e.error}>
                    {e.filename} failed to load
                  </span>
                ))}
              </div>
              {formOpen && script && (
                <div className="pb-form">
                  <p className="order-hint">{script.description}</p>
                  <ParamsForm script={script} values={values} onChange={setValues} />
                  <label className="pb-auto-toggle" title="Simulation only: the runner places the proposals itself during the regular session.">
                    <input type="checkbox" checked={autoExecute} onChange={(e) => setAutoExecute(e.target.checked)} /> auto-execute in the simulation
                  </label>
                  <div className="pb-form-actions">
                    <button type="button" className="generate-button" disabled={starting} onClick={() => void start()}>
                      {starting ? "Starting…" : "Start campaign"}
                    </button>
                  </div>
                  {startError && <p className="order-rejection">{startError}</p>}
                </div>
              )}
            </>
          )
        ) : (
          <p className="order-hint">Select a symbol to start a campaign on it.</p>
        )}
      </div>

      {campaigns.error && <p className="order-rejection">{campaigns.error}</p>}
      {campaigns.loading && campaigns.campaigns.length === 0 && <p className="widget-empty">Loading campaigns…</p>}
      {!campaigns.loading && campaigns.campaigns.length === 0 && (
        <p className="widget-empty">No campaigns yet. Start one above: the playbook then proposes its first step.</p>
      )}
      <ul className="pb-campaigns">
        {ordered.map((c) => (
          <CampaignCard
            key={c.id}
            campaign={c}
            symbol={symbol}
            mode={mode}
            spreads={spreads}
            actions={campaigns}
            onLoad={onLoad}
            onSelectSymbol={onSelectSymbol}
            onRoll={onRoll}
          />
        ))}
      </ul>
      <BacktestPanel symbol={symbol} script={script} params={values} />
      <p className="idea-disclaimer">
        A playbook proposes; you place. Cost basis = (average entry × shares − premiums collected) / shares. Nothing here
        is advice.
      </p>
    </div>
  );
}
