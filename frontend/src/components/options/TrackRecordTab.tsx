import { useCallback, useEffect, useState } from "react";

import { getTrackRecord, refreshTrackRecord } from "../../api/options";
import type { TrackBucket, TrackRecordResponse, TrackReport, TrackSummary } from "../../types/options";
import { formatMoney } from "../../utils/format";
import { NATENBERG, withBook } from "./bookRefs";

const pct = (v: number | null | undefined, digits = 0) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);

/** Actual against predicted: green when the structures won more often than
 * the model said (the seller's edge), red when less, grey when the interval
 * still covers the prediction -- too few to tell. */
function verdictClass(s: TrackSummary): string | undefined {
  if (s.predicted == null || s.ci == null) return undefined;
  if (s.ci[0] > s.predicted) return "delta-up";
  if (s.ci[1] < s.predicted) return "delta-down";
  return undefined;
}

function SummaryCells({ s }: { s: TrackSummary }) {
  return (
    <>
      <td>{s.n}</td>
      <td>{pct(s.predicted)}</td>
      <td className={verdictClass(s)}>{pct(s.actual)}</td>
      <td className="track-ci">{s.ci ? `${pct(s.ci[0])}–${pct(s.ci[1])}` : "—"}</td>
      <td>{s.avg_pnl == null ? "—" : formatMoney(s.avg_pnl)}</td>
      <td>{s.avg_return_on_risk == null ? "—" : pct(s.avg_return_on_risk, 1)}</td>
    </>
  );
}

const SUMMARY_HEAD = (
  <>
    <th title="How many structures">n</th>
    <th title="The mean chance of profit the model gave them">predicted</th>
    <th title="The share that ended in profit, held to expiry and filled at the natural">actual</th>
    <th title="95 % interval of the actual rate (Wilson): where the true rate plausibly lies given n">95 % range</th>
    <th title="Average P/L per structure at expiry">avg P/L</th>
    <th title="Average P/L as a share of each structure's maximum loss">return on risk</th>
  </>
);

function BucketTable({ title, rows, label, hint }: { title: string; rows: TrackBucket[]; label: (b: TrackBucket) => string; hint: string }) {
  if (rows.length === 0) return null;
  return (
    <>
      <h4 title={hint}>{title}</h4>
      <table className="performance-table track-table">
        <thead>
          <tr>
            <th />
            {SUMMARY_HEAD}
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => (
            <tr key={label(b)}>
              <td>{label(b)}</td>
              <SummaryCells s={b} />
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

const chanceLabel = (b: TrackBucket) => `${Math.round(b.range[0] * 100)}–${Math.round(b.range[1] * 100)} %`;
const marginLabel = (b: TrackBucket) =>
  b.range[0] <= -99 ? `below ${b.range[1]} pts` : b.range[1] >= 99 ? `above +${b.range[0]} pts` : `${b.range[0] > 0 ? "+" : ""}${b.range[0]} to ${b.range[1] > 0 ? "+" : ""}${b.range[1]} pts`;

function Report({ r }: { r: TrackReport }) {
  return (
    <>
      <table className="performance-table track-table">
        <thead>
          <tr>
            <th />
            {SUMMARY_HEAD}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>all</td>
            <SummaryCells s={r.overall} />
          </tr>
        </tbody>
      </table>
      <BucketTable
        title="By predicted chance of profit"
        rows={r.by_chance}
        label={chanceLabel}
        hint="Calibration: structures the model gave 60-70 % should win about 60-70 % of the time. Consistently more means the implied volatility overstated the moves (the premium seller's edge); less means it understated them."
      />
      <BucketTable
        title="By breakeven-vol margin"
        rows={r.by_margin}
        label={marginLabel}
        hint="The margin for error the ticket shows: breakeven volatility less the forecast, in vol points, read for the seller. If the margin is worth acting on, the rows above zero should win more and earn more than those below."
      />
      {r.touch && (
        <p className="track-note">
          Touch: predicted {pct(r.touch.predicted)} of {r.touch.n} would trade at their nearest breakeven before
          expiry; {pct(r.touch.actual)} did.
        </p>
      )}
    </>
  );
}

/**
 * Whether the dashboard's predictions came true (backend
 * app/options/track_record.py): the forward record of Screener rows and
 * sent tickets, settled after their expiry, and a reconstruction of the
 * past year from the stored implied volatility.
 */
export function TrackRecordTab() {
  const [data, setData] = useState<TrackRecordResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    try {
      setData(await (refresh ? refreshTrackRecord() : getTrackRecord()));
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <div className="widget-error">{error}</div>;
  if (!data) return <div className="widget-empty">{busy ? "Settling and rebuilding… (the first load reads a year of bars)" : "—"}</div>;
  const f = data.forward;
  const h = data.historical;

  return (
    <div className="track-record">
      <section>
        <h3
          title={withBook(
            "Every valued Screener row (once a day per structure) and every ticket sent, with what was predicted for it, settled at the underlying's close on expiry -- held to expiry, filled at the natural, the same fill the chance assumes.",
            NATENBERG.probability,
          )}
        >
          Your predictions, settled
        </h3>
        {!f.available ? (
          <p className="track-note">Not recording (no prediction store).</p>
        ) : (
          <>
            <p className="track-note">
              {f.recorded} recorded since {f.first_recorded ?? "today"} · {f.resolved} settled · {f.open} open
              {f.next_expiry ? ` (next expiry ${f.next_expiry})` : ""}
              {f.unresolvable ? ` · ${f.unresolvable} not settleable (calendars, missing data)` : ""}
            </p>
            {f.resolved > 0 ? (
              <>
                <Report r={f} />
                {(["screen", "ticket"] as const).map((source) =>
                  f.by_source[source].overall.n > 0 ? (
                    <div key={source}>
                      <h4>{source === "screen" ? "Screener rows" : "Your tickets"}</h4>
                      <Report r={f.by_source[source]} />
                    </div>
                  ) : null,
                )}
              </>
            ) : (
              <p className="track-note">
                Nothing has expired yet. The Screener records its rows every half hour of the session; the first
                answers come with the first expiry.
              </p>
            )}
          </>
        )}
      </section>

      <section>
        <h3
          title={withBook(
            "On every fifth session of the stored implied-volatility history, a condor at 10, 16, 25 and 35 delta (wings at 5) priced flat at that day's at-the-money IV, its chance of profit read off the lognormal that IV implies, settled at the close 45 days later. No smile, no quotes, no bid/ask: this tests the implied volatility as a forecaster of the move, not the pricing of a real chain. Overlapping 45-day windows make neighbouring samples alike, so the intervals are narrower than they should be.",
            NATENBERG.ivAsPredictor,
          )}
        >
          The past year, rebuilt
        </h3>
        {!h.available ? (
          <p className="track-note">{"reason" in h && h.reason ? h.reason : "Not available."}</p>
        ) : (
          <>
            <p className="track-note">
              {h.symbols} symbols · {h.from} to {h.to} · {h.dte}-day condors every {h.step_sessions} sessions · flat
              IV, no skew, no bid/ask{" "}
              <button type="button" className="row-action" disabled={busy} onClick={() => void load(true)}>
                {busy ? "Rebuilding…" : "Rebuild"}
              </button>
            </p>
            <Report r={h} />
            <h4>By short delta</h4>
            <table className="performance-table track-table">
              <thead>
                <tr>
                  <th />
                  {SUMMARY_HEAD}
                </tr>
              </thead>
              <tbody>
                {h.by_delta.map((d) => (
                  <tr key={d.short_delta}>
                    <td>{Math.round(d.short_delta * 100)}Δ</td>
                    <SummaryCells s={d} />
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
    </div>
  );
}
