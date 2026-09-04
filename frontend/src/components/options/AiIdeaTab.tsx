import { useState } from "react";

import { suggestOptionsIdeas } from "../../api/options";
import { formatMoney, formatPrice } from "../../utils/format";
import type { OptionsIdea, OptionsIdeaResponse } from "../../types/options";

interface AiIdeaTabProps {
  symbol: string | null;
  /** Applies an idea's ticket to the widget's own strategy/expiry/legs
   * state. Returns false when the structure could not be loaded (a
   * calendar whose long expiry is not in the strip, say) so the card can
   * say so rather than appearing to do nothing. */
  onLoad: (idea: OptionsIdea) => boolean;
}

/** What the model could see, said plainly. A suggestion made with GEX,
 * a catalyst and an IV rank is a different thing from one made with none
 * of them, and the reader cannot tell the two apart from the prose. */
function ContextUsed({ used }: { used: OptionsIdeaResponse["context_used"] }) {
  const parts = [
    used.has_gex ? "GEX" : null,
    used.has_news ? "news" : null,
    used.has_earnings ? "earnings" : null,
    used.iv_rank_samples > 0 ? `IV history ${used.iv_rank_samples}d` : null,
  ].filter(Boolean);
  return (
    <p className="idea-context">
      Read {used.expiries.length} {used.expiries.length === 1 ? "expiry" : "expiries"}
      {parts.length > 0 ? ` · ${parts.join(" · ")}` : " · no extra context available"}
    </p>
  );
}

function Economics({ idea }: { idea: OptionsIdea }) {
  const { spread } = idea;
  const per = spread.limit_price * 100 * spread.qty;
  return (
    <div className="idea-economics">
      <span>
        {spread.direction === "debit" ? "Pay" : "Receive"} <strong>{formatMoney(per)}</strong>
      </span>
      <span>
        Max profit <strong>{spread.max_profit == null ? "unlimited" : formatMoney(spread.max_profit)}</strong>
      </span>
      <span>
        Max loss <strong>{spread.max_loss == null ? "—" : formatMoney(spread.max_loss)}</strong>
      </span>
      {spread.breakevens.length > 0 && (
        <span>
          Breakeven <strong>{spread.breakevens.map((b) => formatPrice(b)).join(" / ")}</strong>
        </span>
      )}
      <span>
        Collateral <strong>{formatMoney(spread.collateral)}</strong>
      </span>
      <span>{spread.dte}d</span>
    </div>
  );
}

function IdeaCard({ idea, onLoad }: { idea: OptionsIdea; onLoad: (idea: OptionsIdea) => boolean }) {
  const [failed, setFailed] = useState(false);
  const legs = idea.spread.legs
    .map((leg) => `${leg.side === "buy" ? "+" : "−"}${leg.ratio_qty > 1 ? leg.ratio_qty : ""}${leg.strike}${leg.kind[0].toUpperCase()}`)
    .join(" ");

  return (
    <li className="idea-card">
      <div className="idea-card-header">
        <span className="idea-strategy">{idea.strategy_label}</span>
        <span
          className="idea-conviction"
          title="How well the available data supports this structure over the alternatives — not a probability of profit, not a confidence that the trade works out."
        >
          Support {idea.conviction}/10
        </span>
        <button type="button" className="timeframe-button" onClick={() => setFailed(!onLoad(idea))}>
          Load into ticket
        </button>
      </div>
      <div className="idea-legs">
        {legs} · {idea.spread.expiry}
      </div>
      <Economics idea={idea} />
      <p className="idea-reason">{idea.reason}</p>
      <p className="idea-risk">
        <strong>Risk:</strong> {idea.risk_note}
      </p>
      {idea.spread.warnings.map((warning) => (
        <p key={warning} className="idea-warning">
          {warning}
        </p>
      ))}
      {failed && <p className="order-rejection">Could not load this structure into the ticket.</p>}
    </li>
  );
}

/**
 * Claude's take on this underlying's chain: up to three structures, each
 * snapped onto listed strikes and priced by the backend through the same
 * path the ticket uses (see backend app/ai/options_suggest.py). Descriptive
 * annotation, not advice -- and the numbers below are the options stack's,
 * not the model's.
 */
export function AiIdeaTab({ symbol, onLoad }: AiIdeaTabProps) {
  const [result, setResult] = useState<OptionsIdeaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = () => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    suggestOptionsIdeas(symbol)
      .then((res) => setResult(res))
      .catch((err) => setError(String(err instanceof Error ? err.message : err)))
      .finally(() => setLoading(false));
  };

  if (!symbol) {
    return <div className="widget-empty">Select a symbol to ask for option structures.</div>;
  }

  return (
    <div className="idea-tab">
      <div className="idea-toolbar">
        <button type="button" className="generate-button" disabled={loading} onClick={generate}>
          {loading ? "Reading the chain…" : `Suggest structures for ${symbol}`}
        </button>
        {result && result.underlying === symbol && <ContextUsed used={result.context_used} />}
      </div>

      {error && <p className="order-rejection">{error}</p>}

      {loading && (
        // Three expiries of chain plus the context around them, then the
        // model reasoning over all of it -- this is seconds, and a bare
        // spinner would read as broken.
        <p className="widget-empty">Loading the chain, GEX and news for {symbol}, then thinking it over…</p>
      )}

      {!loading && result && result.underlying === symbol && (
        <>
          {result.ideas.length === 0 && result.rejected.length === 0 && (
            <p className="widget-empty">
              Nothing in this chain supports a structure worth describing right now.
            </p>
          )}
          <ul className="idea-list">
            {result.ideas.map((idea, i) => (
              <IdeaCard key={`${idea.strategy}-${idea.spread.expiry}-${i}`} idea={idea} onLoad={onLoad} />
            ))}
          </ul>

          {/* Shown rather than dropped: a shorter list would read as
            * "nothing appeals today", which is a different and wrong
            * statement from "your account cannot trade this one". */}
          {result.rejected.length > 0 && (
            <ul className="idea-rejected">
              {result.rejected.map((rejected, i) => (
                <li key={`${rejected.strategy}-${i}`}>
                  <strong>{rejected.strategy_label}</strong> ({rejected.expiry}) — {rejected.rejected_because}
                </li>
              ))}
            </ul>
          )}

          <p className="idea-disclaimer">{result.disclaimer}</p>
        </>
      )}
    </div>
  );
}
