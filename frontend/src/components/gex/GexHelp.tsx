import { Modal } from "../common/Modal";

/** Every level and every term in the GEX Plan widget, plus the Net GEX
 * badge in the chart header, which reads off the same measurement. The
 * arithmetic it describes lives in backend
 * app/market_data/gamma_exposure.py, the regime and playbook sentences in
 * app/market_data/gex_plan.py, and the percentile in
 * app/market_data/gex_history_store.py; keep them in step when a rule or a
 * threshold changes. */
export function GexHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} title="GEX — the gamma profile" onClose={onClose} className="modal-panel-wide">
      <div className="options-help">
        <p className="options-help-intro">
          Where the open options on this symbol put dealers' hedging pressure, and the price levels that follow from
          it. Descriptive, not advice: it states what the standard GEX heuristics say a regime like this{" "}
          <em>tends</em> to mean. Every number here is arithmetic over the listed chain — none of it is a forecast,
          and the sign convention underneath it is a widely-used convention rather than a settled fact (see{" "}
          <strong>What this rests on</strong>).
        </p>

        <h3>The idea in one paragraph</h3>
        <p className="order-hint">
          Whoever sold you an option hedges it, and gamma decides which way. Short gamma forces them to sell as price
          falls and buy as it rises — hedging that pushes the move further. Long gamma forces the opposite, selling
          rallies and buying dips, which damps the move. Adding up that gamma across every open contract gives one
          signed number per symbol, and per strike it gives a profile: where the hedging pressure sits, and where it
          changes sign.
        </p>

        <h3>How to read it</h3>
        <dl>
          <dt>1. The regime badge first</dt>
          <dd>
            <strong>Negative gamma</strong> — expect follow-through: momentum entries over fading extremes, and a
            stop sized for a calm regime gets clipped by the wider swings. <strong>Positive gamma</strong> — expect
            range: fading the walls over chasing breakouts, tighter stops. This is the sign of net GEX and nothing
            else; no magnitude threshold is invented, because there is no agreed "how big is big" across two
            differently-sized underlyings.
          </dd>
          <dt>2. NEAR FLIP overrides it</dt>
          <dd>
            Spot within 0.5 % of the flip strike. The regime above can turn over during the session here, so the
            playbook built on it is the least reliable it ever is. Read it as "no regime right now", not as a signal.
          </dd>
          <dt>3. The walls as levels, not as predictions</dt>
          <dd>
            They say where hedging flow concentrates, which is why price often slows there. They are not support and
            resistance in the chart-pattern sense and they move as open interest changes.
          </dd>
          <dt>4. The near expiry for today, the walls above for the month</dt>
          <dd>
            The top row is the whole 45-day profile — the month's positioning, slow-moving. The near-expiry row is
            the contracts expiring next, whose gamma per contract is many times sharper and whose walls move during
            the day. Intraday, that row is the one that bites.
          </dd>
        </dl>

        <h3>The levels</h3>
        <dl>
          <dt>Flip (gamma flip)</dt>
          <dd>
            Where the running total of net gamma, summed upward through the strikes, crosses zero — above it the
            profile is net long gamma, below it net short. A <em>proxy</em>: the well-known GEX trackers reprice the
            whole chain's gamma at a range of hypothetical spot prices, which needs a volatility surface this app
            does not have. Blank when the cumulative total never crosses.
          </dd>
          <dt>Call wall</dt>
          <dd>
            The strike carrying the most call-side gamma — conventionally read as overhead resistance, a place a
            rally tends to stall or a profit-take zone.
          </dd>
          <dt>Put wall</dt>
          <dd>
            The strike carrying the most put-side gamma, conventionally read as support. It can land on the same
            strike as the call wall: at the money both sides are usually heaviest, and that is a real answer, not a
            bug.
          </dd>
          <dt>Max pain</dt>
          <dd>
            The strike at which this expiry's open contracts are worth the least in total — sum, at each listed
            strike, what every open contract would pay if price settled there, and take the smallest. It is where
            the most premium expires worthless, read as a magnet into expiry on the argument that the people short
            those contracts hedge toward it. <strong>The arithmetic is exact; the gravitation is folklore.</strong>{" "}
            One expiry at a time, because contracts settling on different days cannot all expire worthless at one
            price, so it hangs off the near-expiry row and is drawn on the chart as{" "}
            <strong>Max pain {"{dte}"}d</strong>. Needs held strikes on both sides; blank otherwise.
          </dd>
        </dl>

        <h3>The near-expiry row</h3>
        <dl>
          <dt>0DTE / 1DTE / the date</dt>
          <dd>
            Today's expiry while the session still trades (before 16:00 New York), the next one after the close.
            Its own walls, its own flip, its own max pain — all computed over that one expiry.
          </dd>
          <dt>gamma solved from quotes</dt>
          <dd>
            Alpaca computes no greeks for a contract expiring today — Black-Scholes divides by time to expiry — so
            for 0DTE the gamma is solved here from each contract's own quote, with the same solver the replayed
            chain uses. When the row says <em>feed greeks</em> instead, the numbers came from the feed.
          </dd>
          <dt>net ±$xxM</dt>
          <dd>
            This expiry's net gamma alone, in millions. Much smaller than the symbol's whole-profile number because
            it is one expiry, not forty-five days of them — do not compare the two directly.
          </dd>
        </dl>

        <h3>Expected move</h3>
        <dl>
          <dt>Expected move to {"{date}"} ±x</dt>
          <dd>
            The at-the-money straddle's mid to that expiry: what the option market charges for the move, and under
            Black-Scholes that price <em>is</em> the average absolute move. The market's own number, not this app's.
          </dd>
          <dt>1σ · Band</dt>
          <dd>
            One sigma is the straddle times 1.25, the range that contains about 68 % of outcomes under the model;
            the band is spot ± that. Symmetric around spot — skew is ignored, as every straddle-based expected move
            ignores it. Drawn on the chart as the EM lines.
          </dd>
        </dl>

        <h3>Net GEX and its percentile</h3>
        <dl>
          <dt>Net GEX ±$x.xxB</dt>
          <dd>
            The badge in the chart header: the whole profile's signed dollar gamma, summed over all strikes. Per
            contract it is gamma × open interest × 100 × spot² × 1 %, calls positive and puts negative.
          </dd>
          <dt>· xx %</dt>
          <dd>
            Where today's reading sits between the lowest and highest recorded for <em>this</em> symbol — because a
            raw dollar figure is unreadable on its own. Minus four billion means nothing until you know the symbol
            has run between minus eight and plus two; the tooltip gives that range. High means long gamma for this
            symbol by its own standards, low means short.
          </dd>
          <dt>"not yet (N sessions recorded, 20 needed)"</dt>
          <dd>
            One value is recorded per symbol and session, as a side effect of your looking at it, and below twenty
            sessions the highest and lowest readings are a sample artefact rather than a range. Until then there is
            no percentile at all — "not known", never a middling fifty. The same floor and the same arithmetic as
            the IV rank.
          </dd>
        </dl>

        <h3>What this rests on</h3>
        <dl>
          <dt>strikes · open interest</dt>
          <dd>
            The line at the bottom, always. A wall built from a handful of contracts is still a real number, but it
            is not the same kind of evidence as one built from tens of thousands of open contracts. Below 20 strikes
            or 5 000 open contracts the line says <strong>Thin chain</strong> — a legibility warning, it suppresses
            nothing.
          </dd>
          <dt>Open interest is yesterday's</dt>
          <dd>
            The clearing house publishes it overnight, so every number here describes positions taken before today.
            Today's new 0DTE positions — often the ones that matter most — are not in it.
          </dd>
          <dt>The sign convention is a convention</dt>
          <dd>
            Calls positive, puts negative assumes dealers are long calls and short puts to the public. It is what
            the popular free GEX trackers use, and the community genuinely disagrees about it. If that assumption is
            wrong for a given symbol, the sign of everything here flips with it.
          </dd>
          <dt>A bounded chain</dt>
          <dd>
            Expiries out to 45 days, strikes within ±15 % of spot. Far-dated LEAPS carry real open interest but say
            little about the market's <em>current</em> gamma, and an unbounded chain means an unbounded number of API
            calls. The whole-profile numbers also leave today's expiry out; the near-expiry row covers it.
          </dd>
        </dl>

        <h3>When there is nothing to show</h3>
        <dl>
          <dt>"Select a symbol"</dt>
          <dd>The widget follows whatever is active on the dashboard — chart, scanner row, watchlist click.</dd>
          <dt>"Reading … options chain"</dt>
          <dd>
            Any optionable symbol works, not a fixed list, and one the backend has not computed before takes seconds
            rather than milliseconds.
          </dd>
          <dt>"No GEX for …"</dt>
          <dd>
            It needs Alpaca credentials and a listed options chain. Index and futures options are not available at
            all through this broker.
          </dd>
        </dl>
      </div>
    </Modal>
  );
}
