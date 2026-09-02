import { Fragment, useState } from "react";

import { useJournal, type JournaledTrade } from "../../hooks/useJournal";
import { modeBadge } from "../../api/tradingMode";
import { useTradingMode } from "../../hooks/useTradingMode";
import type { ChartFocus } from "../../types/screener";
import { formatPrice } from "../../utils/format";
import { signedNumber, tradeTime } from "./TradingWidget";

/** Same intraday-vs-daily heuristic ScreenBacktestPanel uses for a backtest
 * pick's timeframeKey: a trade that opened and closed the same day needs an
 * intraday chart to see both arrows on, a multi-day swing is lost on one. */
function focusFor(trade: JournaledTrade): ChartFocus {
  const entryTime = Math.floor(new Date(trade.opened_at).getTime() / 1000);
  const exitTime = Math.floor(new Date(trade.closed_at).getTime() / 1000);
  return {
    symbol: trade.symbol,
    time: entryTime,
    timeframeKey: exitTime - entryTime < 24 * 60 * 60 ? "5m" : "1D",
    trade: { exitTime, won: trade.pnl >= 0 },
  };
}

const STARS = [1, 2, 3, 4, 5];

interface Draft {
  note: string;
  rating: number | null;
  tags: string[];
}

function draftFrom(trade: JournaledTrade): Draft {
  return {
    note: trade.journal?.note ?? "",
    rating: trade.journal?.rating ?? null,
    tags: trade.journal?.tags ?? [],
  };
}

function StarRating({ value, onChange }: { value: number | null; onChange: (next: number | null) => void }) {
  return (
    <span className="journal-stars">
      {STARS.map((n) => (
        <button
          key={n}
          type="button"
          className="journal-star"
          aria-pressed={value !== null && n <= value}
          onClick={() => onChange(value === n ? null : n)}
          title={`${n} star${n === 1 ? "" : "s"}`}
        >
          {value !== null && n <= value ? "★" : "☆"}
        </button>
      ))}
    </span>
  );
}

function TagChips({ tags, onChange }: { tags: string[]; onChange: (next: string[]) => void }) {
  const [draft, setDraft] = useState("");

  function addTag() {
    const tag = draft.trim();
    if (tag && !tags.includes(tag) && tags.length < 10) onChange([...tags, tag]);
    setDraft("");
  }

  return (
    <div className="journal-tags">
      {tags.map((tag) => (
        <span key={tag} className="journal-tag-chip">
          {tag}
          <button type="button" onClick={() => onChange(tags.filter((t) => t !== tag))} title={`Remove ${tag}`}>
            ×
          </button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            addTag();
          }
        }}
        onBlur={addTag}
        placeholder="Add tag…"
        maxLength={30}
      />
    </div>
  );
}

function JournalEditor({
  trade,
  onSave,
  onCancel,
}: {
  trade: JournaledTrade;
  onSave: (draft: Draft) => Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(trade));
  const [saving, setSaving] = useState(false);

  return (
    <div className="journal-editor">
      <textarea
        className="journal-note-input"
        value={draft.note}
        onChange={(e) => setDraft((d) => ({ ...d, note: e.target.value }))}
        placeholder="What was the plan? What actually happened?"
        maxLength={2000}
        rows={4}
      />
      <div className="journal-editor-row">
        <StarRating value={draft.rating} onChange={(rating) => setDraft((d) => ({ ...d, rating }))} />
        <TagChips tags={draft.tags} onChange={(tags) => setDraft((d) => ({ ...d, tags }))} />
      </div>
      <div className="journal-editor-actions">
        <button type="button" className="timeframe-button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        <button
          type="button"
          className="timeframe-button"
          aria-pressed
          disabled={saving}
          onClick={async () => {
            setSaving(true);
            try {
              await onSave(draft);
            } finally {
              setSaving(false);
            }
          }}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

/**
 * Notes on closed trades -- free text, a 1-5 self-rating, and freeform tags
 * per trade. See backend app.trading.journal_store. Sourced from the same
 * trade list TradingWidget's Trades tab shows (useJournal -> getTrades()),
 * which is already trading-mode-aware: only Simulation Mode's trades are
 * actually this user's own (SimStore is per-user), so the header surfaces
 * the current mode and a hint appears when journaling the shared real
 * account's history instead. Clicking a row also loads that trade's chart,
 * scrolled to show it with Entry/Exit arrows (see ChartFocus's `trade`
 * field, CandleChart's focusTrade prop).
 */
export function TradeJournalWidget({ onSelectPick }: { onSelectPick: (focus: ChartFocus) => void }) {
  const { rows, loading, error, saveEntry } = useJournal();
  const { mode } = useTradingMode();
  const [editingId, setEditingId] = useState<string | null>(null);

  const { className: badgeClass, label: badgeLabel } = modeBadge(mode);

  return (
    <div className="widget trade-journal-widget">
      <div className="widget-header">
        <h2>Trading Journal</h2>
        <span className={`trading-mode-badge ${badgeClass}`}>{badgeLabel}</span>
      </div>
      <div className="widget-body">
        {mode !== "simulation" && (
          <p className="journal-mode-hint">
            Journaling the shared account's trade history -- these trades aren't personal to your
            login. Switch to Simulation Mode for a trade history that's only yours.
          </p>
        )}
        {loading && rows.length === 0 ? (
          <div className="widget-empty">Loading…</div>
        ) : error ? (
          <div className="widget-empty">{error}</div>
        ) : rows.length === 0 ? (
          <div className="widget-empty">No closed trades to journal yet.</div>
        ) : (
          <table className="performance-table">
            <thead>
              <tr>
                <th>Closed</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>P&amp;L</th>
                <th>R</th>
                <th>Rating</th>
                <th>Tags</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((trade) => {
                const pnl = signedNumber(trade.pnl, 2);
                const r = signedNumber(trade.r_multiple, 2, "R");
                const isEditing = editingId === trade.id;
                return (
                  <Fragment key={trade.id}>
                    <tr
                      aria-selected={isEditing}
                      onClick={() => {
                        setEditingId(isEditing ? null : trade.id);
                        onSelectPick(focusFor(trade));
                      }}
                      title={`Entry ${formatPrice(trade.entry_avg)} · Exit ${formatPrice(trade.exit_avg)} -- click to load the chart`}
                    >
                      <td>{tradeTime(trade.closed_at)}</td>
                      <td className="symbol-cell">{trade.symbol}</td>
                      <td>{trade.side}</td>
                      <td className={pnl.cls}>{pnl.text}</td>
                      <td className={r.cls}>{r.text}</td>
                      <td>{trade.journal?.rating ? "★".repeat(trade.journal.rating) : "—"}</td>
                      <td className="journal-tags-preview">{trade.journal?.tags.join(", ") || "—"}</td>
                      <td className="journal-note-preview">{trade.journal?.note || "—"}</td>
                    </tr>
                    {isEditing && (
                      <tr>
                        <td colSpan={8}>
                          <JournalEditor
                            trade={trade}
                            onCancel={() => setEditingId(null)}
                            onSave={async (draft) => {
                              await saveEntry(trade.id, draft);
                              setEditingId(null);
                            }}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
