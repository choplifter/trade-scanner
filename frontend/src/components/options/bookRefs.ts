/**
 * Where the idea behind a figure is laid out in Sheldon Natenberg, *Option
 * Volatility and Pricing*, 2nd ed. Chapter and section titles, which hold in
 * every printing; the PDF page is the e-book copy kept beside the repo (it
 * reflows to 1173 pages and carries no printed page numbers).
 *
 * Shown in the help panels (BookRef below) and appended to tooltips
 * (withBook). Each entry names the section that explains the thing the
 * dashboard does, not a passage it quotes.
 */
import { createElement } from "react";

export interface BookRef {
  chapter: number;
  chapterTitle: string;
  section?: string;
  pdfPage: number;
}

const ref = (chapter: number, chapterTitle: string, section: string | undefined, pdfPage: number): BookRef => ({
  chapter,
  chapterTitle,
  section,
  pdfPage,
});

export const NATENBERG = {
  probability: ref(5, "Theoretical Pricing Models", "The Importance of Probability", 109),
  volatilityAsStdDev: ref(6, "Volatility", "Volatility as a Standard Deviation", 154),
  scalingForTime: ref(6, "Volatility", "Scaling Volatility for Time", 156),
  interpretingVol: ref(6, "Volatility", "Interpreting Volatility Data", 168),
  greeks: ref(7, "Risk Measurement I", undefined, 194),
  delta: ref(7, "Risk Measurement I", "The Delta", 202),
  theta: ref(7, "Risk Measurement I", "The Theta", 216),
  vega: ref(7, "Risk Measurement I", "The Vega", 223),
  straddle: ref(11, "Volatility Spreads", "Straddle", 344),
  strangle: ref(11, "Volatility Spreads", "Strangle", 349),
  butterfly: ref(11, "Volatility Spreads", "Butterfly", 354),
  condor: ref(11, "Volatility Spreads", "Condor", 360),
  calendar: ref(11, "Volatility Spreads", "Calendar Spread", 387),
  diagonal: ref(11, "Volatility Spreads", "Diagonal Spreads", 412),
  choosingStrategy: ref(11, "Volatility Spreads", "Choosing an Appropriate Strategy", 421),
  adjustments: ref(11, "Volatility Spreads", "Adjustments", 432),
  verticals: ref(12, "Bull and Bear Spreads", undefined, 438),
  volatilityRisk: ref(13, "Risk Considerations", "Volatility Risk", 476),
  marginForError: ref(13, "Risk Considerations", "How Much Margin for Error?", 498),
  goodSpread: ref(13, "Risk Considerations", "What Is a Good Spread?", 512),
  earlyExerciseCalls: ref(16, "Early Exercise of American Options", "Early Exercise of Call Options on Stock", 608),
  earlyExercisePuts: ref(16, "Early Exercise of American Options", "Early Exercise of Put Options on Stock", 612),
  coveredWrites: ref(17, "Hedging with Options", undefined, 648),
  historicalVol: ref(20, "Volatility Revisited", "Historical Volatility", 773),
  volForecasting: ref(20, "Volatility Revisited", "Volatility Forecasting", 794),
  ivAsPredictor: ref(20, "Volatility Revisited", "Implied Volatility as a Predictor of Future Volatility", 799),
  forwardVol: ref(20, "Volatility Revisited", "Forward Volatility", 823),
  positionAnalysis: ref(21, "Position Analysis", undefined, 835),
  realWorld: ref(23, "Models and the Real World", undefined, 937),
  gaps: ref(23, "Models and the Real World", "Trading Is Continuous", 953),
  pinRisk: ref(23, "Models and the Real World", "Expiration Straddles", 962),
  skew: ref(24, "Volatility Skews", "Modeling the Skew", 987),
  skewedRisk: ref(24, "Volatility Skews", "Skewed Risk Measures", 1009),
  impliedDistributions: ref(24, "Volatility Skews", "Implied Distributions", 1024),
} satisfies Record<string, BookRef>;

/** "Natenberg, ch. 24 "Volatility Skews" — Implied Distributions (PDF p. 1024)" */
export function bookRefText(r: BookRef): string {
  const where = r.section ? ` — ${r.section}` : "";
  return `Natenberg, ch. ${r.chapter} "${r.chapterTitle}"${where} (PDF p. ${r.pdfPage})`;
}

/** A tooltip with where to read more appended on its own line. */
export function withBook(title: string, ...refs: BookRef[]): string {
  return refs.length ? `${title}\n\nIn the book: ${refs.map(bookRefText).join("; ")}` : title;
}

/** The same, as a quiet line under a help entry. */
export function BookRefLine({ refs }: { refs: BookRef[] }) {
  return createElement("span", { className: "book-ref" }, `📖 ${refs.map(bookRefText).join(" · ")}`);
}
