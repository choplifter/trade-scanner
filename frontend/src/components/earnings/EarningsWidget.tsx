import { useMemo, useState } from "react";

import { useEarningsEvaluation } from "../../hooks/useEarningsEvaluation";
import { useEarningsScreen } from "../../hooks/useEarningsScreen";
import { isPriced, type EarningsEvaluation, type EarningsScreenRow, type FamilyScore, type SymbolFacts } from "../../types/earnings";
import type { LoadableStructure, OptimizeResponse } from "../../types/options";
import { symbolDragProps } from "../../utils/dragSymbol";
import { formatNum, formatPct } from "../../utils/format";
import { formatExpiry } from "../../utils/occ";
import { requestOptimizer } from "../options/optimizerIntent";
import { ResultCard } from "../options/ResultCard";
import { requestTicket } from "../options/ticketIntent";
import { EarningsHelp } from "./EarningsHelp";

/** Above this the market is charging more for the print than the stock has
 * historically delivered; below the lower one, less. The same thresholds
 * the backend's rule matrix uses -- shown here as a tint, not a verdict. */
const RICH_RATIO = 1.15;
const CHEAP_RATIO = 0.85;

/** The optimizer's drop reasons in prose -- the same wording the
 * Optimizer tab's skipped line uses. */
const SKIP_REASONS: Record<string, string> = {
  non_positive_return: "lose at the target",
  under_min_risk: "under $5 of risk",
  over_budget: "over budget",
  over_max_loss: "over max loss",
  no_market: "no market",
  no_iv: "no IV",
  wrong_way_market: "quoted the wrong way",
  risk_shape: "mispriced shape",
  candidate_cap: "beyond the candidate cap",
  strategy_cap: "duplicates of a better one",
};

type SortKey = "liquidity" | "symbol" | "implied" | "ratio";

function ratioOf(facts: SymbolFacts | undefined): number | null {
  if (!facts || facts.implied_move_pct == null || !facts.hist_median_pct || facts.samples < 2) return null;
  return facts.implied_move_pct / facts.hist_median_pct;
}

function ratioClass(ratio: number | null): string {
  if (ratio == null) return "";
  if (ratio >= RICH_RATIO) return " rich";
  if (ratio <= CHEAP_RATIO) return " cheap";
  return "";
}

function dollars(value: number | null): string {
  if (value == null) return "—";
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(0)}M`;
  return formatNum(value, 0);
}

/** The signals line: what the matrix read, in the order it matters. Every
 * missing value is shown as an em dash rather than left out, so "not
 * known" is visible instead of looking like a value of zero. */
function SignalsLine({ evaluation }: { evaluation: EarningsEvaluation }) {
  const s = evaluation.signals;
  const bits: [string, string, string][] = [
    ["Implied move", s.implied_move_pct == null ? "—" : `±${s.implied_move_pct.toFixed(1)} %`, "One standard deviation to the expiry that settles after the report, from its at-the-money implied volatility. On the report day this includes the ordinary sessions to expiry, not the print alone."],
    ["Past reports", s.hist_median_pct == null ? "—" : `±${s.hist_median_pct.toFixed(1)} % median of ${s.samples}`, "The stock's own close-to-close move across each of its past reports: the close before to the first close after."],
    ["Ratio", s.move_ratio == null ? "—" : `x${s.move_ratio.toFixed(2)}`, "Implied move over the median past one. Above 1.15 the market charges more than the stock has delivered; below 0.85, less."],
    ["IV vs realised", s.iv_over_realized == null ? "—" : `x${s.iv_over_realized.toFixed(2)}`, "At-the-money implied volatility over the stock's 20-day realised volatility."],
    ["IV rank", s.iv_rank_pct == null ? `— (${s.iv_rank_samples} sessions)` : `${s.iv_rank_pct.toFixed(0)}`, "Where today's implied volatility sits in this symbol's own recorded range. Needs 20 recorded sessions; until then it is unknown, not unremarkable."],
    ["Term", s.term_slope == null ? "—" : `x${s.term_slope.toFixed(2)} front/back`, "The horizon expiry's at-the-money implied volatility over a later one's. Above 1.25 the near expiry carries the event premium."],
    ["Skew", s.skew == null ? "—" : formatPct(s.skew * 100), "Put implied volatility minus call implied volatility the same distance from spot. Positive is the usual equity shape."],
    ["GEX", s.gex_regime == null ? "—" : `${s.gex_regime}${s.near_flip ? ", near flip" : ""}`, "Dealer gamma positioning. Net long gamma tends to damp moves, net short to amplify them. Absent means no reading, not neutral positioning."],
    ["Wings", s.wing_room == null ? "—" : `${s.offerable_puts_below}P / ${s.offerable_calls_above}C`, "Quotable strikes beyond the expected move on each side -- what a four-legged structure needs to exist at all."],
  ];
  return (
    <div className="earn-signals">
      {bits.map(([label, value, title]) => (
        <span key={label} className="earn-signal" title={title}>
          <span className="earn-signal-label">{label}</span>
          <strong>{value}</strong>
        </span>
      ))}
    </div>
  );
}

function ScoreRow({ score, symbol, onSelectSymbol }: { score: FamilyScore; symbol: string; onSelectSymbol?: (s: string) => void }) {
  return (
    <li className={`earn-score${score.score > 0 ? " positive" : score.score < 0 ? " negative" : ""}`}>
      <div className="earn-score-head">
        <strong>{score.label}</strong>
        <span className="earn-score-points" title="How many of the available signals point at this family and how strongly, relative to the others. Not a probability and not a recommendation.">
          {score.score > 0 ? `+${score.score}` : score.score}
        </span>
        {score.open_optimizer && (
          <button
            type="button"
            className="row-action"
            title="Open the Optimizer on this symbol with this family and horizon already set."
            onClick={() => {
              onSelectSymbol?.(symbol);
              requestOptimizer({
                symbol,
                outlook: (score.open_optimizer?.outlook as never) ?? "neutral",
                reason: `${score.label} from the earnings screen`,
                request: score.open_optimizer ?? undefined,
              });
            }}
          >
            Open optimizer
          </button>
        )}
      </div>
      {score.not_priced_because && <p className="earn-score-note">Not priced here: {score.not_priced_because}.</p>}
      <ul className="earn-score-reasons">
        {score.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
    </li>
  );
}

function Priced({ run, symbol, onSelectSymbol }: { run: OptimizeResponse; symbol: string; onSelectSymbol?: (s: string) => void }) {
  const bestRor = Math.max(0, ...run.results.map((r) => r.return_on_risk));
  const bestChance = Math.max(0, ...run.results.map((r) => r.chance ?? 0));
  if (run.results.length === 0) {
    // Why nothing survived matters more here than in the Optimizer tab:
    // "every shape loses at a move this size" is the answer, not a gap.
    const drops = Object.entries(run.skipped.reasons)
      .filter(([, n]) => n > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([k, n]) => `${n} ${SKIP_REASONS[k] ?? k}`)
      .join(", ");
    return (
      <p className="widget-empty">
        No structure in these families pays at every point of a ±{(((run.target.high - run.target.low) / 2 / run.spot) * 100).toFixed(1)} %
        move. {run.skipped.total} shape(s) enumerated{drops ? `: ${drops}` : ""}.
        {run.rejected.length > 0 && ` ${run.rejected[0].rejected_because}.`}
      </p>
    );
  }
  return (
    <ul className="opt-grid">
      {run.results.map((r) => (
        <ResultCard
          key={`${r.strategy}-${r.expiry}-${r.legs_label}`}
          r={r}
          bestRor={bestRor}
          bestChance={bestChance}
          targets={run.target.points}
          onLoad={(structure: LoadableStructure) => {
            onSelectSymbol?.(symbol);
            requestTicket({ symbol, structure });
            return true;
          }}
        />
      ))}
    </ul>
  );
}

function Detail({
  symbol,
  evaluation,
  error,
  pending,
  onSelectSymbol,
}: {
  symbol: string;
  evaluation: EarningsEvaluation | undefined;
  error: string | undefined;
  pending: boolean;
  onSelectSymbol?: (s: string) => void;
}) {
  if (pending) return <p className="widget-empty">Reading the chain, the past reports and the positioning…</p>;
  if (error) return <p className="order-rejection">{error}</p>;
  if (!evaluation) return null;

  const neutral = evaluation.optimizer.neutral;
  const volatility = evaluation.optimizer.volatility;
  return (
    <div className="earn-detail">
      <SignalsLine evaluation={evaluation} />
      {evaluation.warnings.map((w) => (
        <p key={w} className="idea-warning">
          {w}
        </p>
      ))}
      <p className="idea-context">
        Horizon {formatExpiry(evaluation.horizon_expiry)} ({evaluation.horizon_dte}d), the first expiry that settles after the report
        {evaluation.target.neutral && ` · target ±${(evaluation.target.neutral.move * 100).toFixed(1)} % (${evaluation.target.neutral.basis})`}
      </p>
      <ul className="earn-scores">
        {evaluation.scores
          .filter((s) => s.score !== 0)
          .map((score) => (
            <ScoreRow key={score.family} score={score} symbol={symbol} onSelectSymbol={onSelectSymbol} />
          ))}
      </ul>
      {isPriced(neutral) && <Priced run={neutral} symbol={symbol} onSelectSymbol={onSelectSymbol} />}
      {neutral && !isPriced(neutral) && <p className="order-rejection">{neutral.error}</p>}
      {isPriced(volatility) && <Priced run={volatility} symbol={symbol} onSelectSymbol={onSelectSymbol} />}
      {volatility && !isPriced(volatility) && <p className="order-rejection">{volatility.error}</p>}
      <p className="idea-disclaimer">{evaluation.disclaimer}</p>
    </div>
  );
}

interface EarningsWidgetProps {
  onSelectSymbol?: (symbol: string) => void;
  selectedSymbol?: string | null;
}

/**
 * Who reports today and next session, and -- on request, per symbol --
 * which option structures the signals argue for.
 *
 * The list is deliberately cheap and the evaluation deliberately not: a
 * row costs a calendar lookup, expanding one costs two chains, a gamma
 * profile and a dozen previews. So nothing is evaluated until the reader
 * opens it.
 */
export default function EarningsWidget({ onSelectSymbol, selectedSymbol }: EarningsWidgetProps) {
  const { screen, facts, loading, factsLoading, error, refresh } = useEarningsScreen();
  const evaluation = useEarningsEvaluation();
  const [open, setOpen] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("liquidity");
  const [helpOpen, setHelpOpen] = useState(false);

  const rows = useMemo(() => {
    const list = [...(screen?.rows ?? [])];
    list.sort((a, b) => {
      if (sort === "symbol") return a.symbol.localeCompare(b.symbol);
      if (sort === "implied") return (facts[b.symbol]?.implied_move_pct ?? -1) - (facts[a.symbol]?.implied_move_pct ?? -1);
      if (sort === "ratio") return (ratioOf(facts[b.symbol]) ?? -1) - (ratioOf(facts[a.symbol]) ?? -1);
      return b.avg_dollar_vol_20d - a.avg_dollar_vol_20d;
    });
    return list;
  }, [screen, facts, sort]);

  const toggle = (row: EarningsScreenRow) => {
    onSelectSymbol?.(row.symbol);
    if (open === row.symbol) {
      setOpen(null);
      return;
    }
    setOpen(row.symbol);
    evaluation.run(row.symbol);
  };

  const header = (key: SortKey, label: string, title: string) => (
    <th className={sort === key ? "sorted" : undefined} title={title} onClick={() => setSort(key)}>
      {label}
    </th>
  );

  return (
    <div className="widget earnings-widget">
      <div className="widget-header">
        <h2>
          Earnings
          {screen && <span className="widget-count">{rows.length}</span>}
        </h2>
        <div className="timeframe-selector">
          {screen && (
            <span className="order-hint" title="Reports dated today or on the next trading session. A company that reports after the close moves the next session, and FMP does not give the timing reliably, so both days are listed.">
              {screen.today} · next {screen.next_session}
            </span>
          )}
          <button type="button" className="timeframe-button" onClick={refresh} aria-busy={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
          <button
            type="button"
            className="timeframe-button options-help-button"
            onClick={() => setHelpOpen(true)}
            title="What everything in this widget means"
            aria-label="Help"
          >
            ?
          </button>
        </div>
        <EarningsHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
      </div>
      <div className="widget-body">
        {error && <p className="order-rejection">{error}</p>}
        {screen && !screen.sources.calendar && (
          <p className="idea-warning">
            The earnings calendar could not be read (no FMP key, or the call failed). An empty list here means "not known",
            not "nobody reports today".
          </p>
        )}
        {!screen && loading && <p className="widget-empty">Loading the calendar…</p>}
        {screen && rows.length === 0 && screen.sources.calendar && (
          <p className="widget-empty">
            No liquid reporters today or next session. {screen.candidates} name(s) were listed before the liquidity cut.
          </p>
        )}
        {rows.length > 0 && (
          <table className="earn-table">
            <thead>
              <tr>
                {header("symbol", "Symbol", "Sort by symbol. Clicking a row selects it on the dashboard and opens its evaluation.")}
                <th title="Which session the report falls on, and whether the company has already published.">When</th>
                <th title="The live price when the scanner has a row for this symbol, otherwise the last close.">Last</th>
                <th title="Change today, when the scanner is tracking this symbol.">Chg</th>
                {header("liquidity", "Avg $vol", "Sort by 20-session average dollar volume -- the liquidity that decides whether the options are worth reading at all.")}
                <th title="Strikes listed near the money on the front expiry: a coarse read on whether the chain can carry a structure.">Strikes</th>
                {header("implied", "Implied", "Sort by the implied move: one standard deviation to the front expiry.")}
                <th title="The stock's median close-to-close move across its past reports.">Past</th>
                {header("ratio", "Ratio", "Sort by implied over median past move. Above 1.15 the market charges more than the stock has delivered (green); below 0.85, less (amber).")}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const fact = facts[row.symbol];
                const ratio = ratioOf(fact);
                const isOpen = open === row.symbol;
                return [
                  <tr
                    key={row.symbol}
                    className={`${isOpen ? "expanded " : ""}${selectedSymbol === row.symbol ? "selected" : ""}`}
                    // Draggable onto the chart, the watchlist or the options
                    // widget, the same as a scanner row.
                    {...symbolDragProps(row.symbol)}
                    onClick={() => toggle(row)}
                    aria-expanded={isOpen}
                  >
                    <td className="earn-symbol">
                      <strong>{row.symbol}</strong>
                      {row.company_name && <span className="earn-company">{row.company_name}</span>}
                    </td>
                    <td>
                      <span className={`earn-session-chip ${row.session}`}>{row.session === "today" ? "Today" : "Next"}</span>
                      {row.reported && (
                        <span className="earn-reported" title="Already published: the event premium has been paid out.">
                          ✓
                        </span>
                      )}
                    </td>
                    <td>{row.last == null ? "—" : row.last.toFixed(2)}</td>
                    <td className={row.pct_change == null ? undefined : row.pct_change >= 0 ? "delta-up" : "delta-down"}>
                      {row.pct_change == null ? "—" : formatPct(row.pct_change)}
                    </td>
                    <td>{dollars(row.avg_dollar_vol_20d)}</td>
                    <td>{fact?.contract_count ?? (factsLoading ? "…" : "—")}</td>
                    <td>{fact?.implied_move_pct == null ? (factsLoading ? "…" : "—") : `±${fact.implied_move_pct.toFixed(1)}%`}</td>
                    <td title={fact?.history_note ?? undefined}>
                      {fact?.hist_median_pct == null ? (factsLoading ? "…" : "—") : `±${fact.hist_median_pct.toFixed(1)}%`}
                    </td>
                    <td className={`earn-ratio${ratioClass(ratio)}`}>{ratio == null ? "—" : `x${ratio.toFixed(2)}`}</td>
                  </tr>,
                  isOpen ? (
                    <tr key={`${row.symbol}-detail`} className="earn-detail-row">
                      <td colSpan={9}>
                        <Detail
                          symbol={row.symbol}
                          evaluation={evaluation.bySymbol[row.symbol]}
                          error={evaluation.errors[row.symbol]}
                          pending={evaluation.pending === row.symbol}
                          onSelectSymbol={onSelectSymbol}
                        />
                      </td>
                    </tr>
                  ) : null,
                ];
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
