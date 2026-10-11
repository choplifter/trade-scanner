/** What the options widgets' abbreviations stand for, put in front of the
 * tooltip of any header or field whose label uses one -- so "EV" never has
 * to be guessed. One line each: the long form, then the plain meaning. */

const TERMS: { pattern: RegExp; text: string }[] = [
  { pattern: /\bEV\b/, text: "EV = expected value (Erwartungswert): the average P/L over all outcomes, each weighted by its probability." },
  { pattern: /\bBE vol\b|\bBE margin\b/, text: "BE vol = breakeven volatility: the volatility at which the price is exactly fair; BE margin = how far it sits past the forecast." },
  { pattern: /\bBE\b(?! vol| margin)/, text: "BE = breakeven: the underlying price at expiry where the structure neither makes nor loses money." },
  { pattern: /\bIV rank\b/, text: "IV rank = where today's implied volatility sits between its lowest (0 %) and highest (100 %) of the past year." },
  { pattern: /\bIV\b(?! rank)/, text: "IV = implied volatility: the yearly move the option prices imply." },
  { pattern: /\bRV\b/, text: "RV = realised volatility: how much the stock actually moved, annualised (here: the last 20 sessions)." },
  { pattern: /\bOI\b/, text: "OI = open interest: contracts outstanding (positions still open)." },
  { pattern: /\bVol\b(?!\w)/, text: "Vol = volume: contracts traded in this session." },
  { pattern: /\b0DTE\b/, text: "0DTE = zero days to expiry: options expiring today." },
  { pattern: /\bDTE\b/, text: "DTE = days to expiry." },
  { pattern: /Δ/, text: "Δ = delta: how much the option moves per 1 $ of the underlying; roughly its chance of finishing in the money." },
  { pattern: /Γ/, text: "Γ = gamma: how much delta changes per 1 $ of the underlying." },
  { pattern: /Θ/, text: "Θ = theta: value lost per day as time passes." },
  { pattern: /^V$|\bV \$/, text: "V = vega: value gained per point of implied volatility." },
  { pattern: /σ/, text: "σ = sigma, one standard deviation of the move to expiry (about 68 % of outcomes fall within ±1σ)." },
  { pattern: /R\/R/, text: "R/R = risk / reward: the most it can lose against the most it can make." },
  { pattern: /P\s?\/\s?L/, text: "P/L = profit and loss." },
  { pattern: /\bATM\b/, text: "ATM = at the money: the strike nearest the underlying's price." },
  { pattern: /\bnat\b|\bNatural\b/, text: "Natural = every leg filled at its worse side: sold at the bid, bought at the ask." },
  { pattern: /\bMid\b|\bmid\b/, text: "Mid = the midpoint between bid and ask." },
  { pattern: /\bBC\b/, text: "BC = Barchart's own figure, as in the export." },
];

/** The long forms of the abbreviations `label` uses, one per line. */
export function glossFor(label: string): string[] {
  return TERMS.filter((t) => t.pattern.test(label)).map((t) => t.text);
}

/** `title` with the long forms of `label`'s abbreviations in front. */
export function withGloss(label: string, title?: string): string | undefined {
  const lines = glossFor(label);
  if (!lines.length) return title;
  return title ? `${lines.join("\n")}\n\n${title}` : lines.join("\n");
}
