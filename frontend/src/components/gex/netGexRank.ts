import type { NetGexRank } from "../../types/gex";

/** What the net-GEX percentile says, in one sentence for a tooltip.
 *
 * Phrased like ivRankSentence() in options/eventMarks.ts, and for the same
 * reason: a number on its own ("-0.4B") is unreadable until it is placed in
 * the range the symbol has actually run. Below the 20-session floor the
 * backend sends no rank, and this says how many sessions are recorded
 * rather than inventing a middling fifty. */
export function netGexRankSentence(rank: NetGexRank | null | undefined): string {
  const billions = (value: number) => `${value >= 0 ? "+" : "-"}$${(Math.abs(value) / 1e9).toFixed(2)}B`;
  if (rank?.rank) {
    const { percent, low, high, samples } = rank.rank;
    const gloss = percent >= 80 ? "long gamma for this symbol" : percent <= 20 ? "short gamma for this symbol" : "mid-range";
    return `${percent.toFixed(0)} % of its own range · ${gloss} (${billions(low)} to ${billions(high)} over ${samples} sessions).`;
  }
  const samples = rank?.samples ?? 0;
  return `Percentile: not yet (${samples} session${samples === 1 ? "" : "s"} recorded, 20 needed).`;
}
