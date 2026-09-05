import { Modal } from "../common/Modal";

interface OptionsHelpProps {
  open: boolean;
  onClose: () => void;
}

/** Every term on the Options widget, in the order the eye meets them:
 * header, expiry strip, chain, ticket, risk chart, Open spreads, Idea. A
 * reference for the reader who knows what an iron condor is but not what
 * this widget means by Width, Short Δ or Min credit. The mechanics it
 * describes live in SpreadTicket, legPicker and app/options; keep the
 * three in step when one changes. */
export function OptionsHelp({ open, onClose }: OptionsHelpProps) {
  return (
    <Modal open={open} title="The Options widget" onClose={onClose} className="modal-panel-wide">
      <div className="options-help">
        <p className="options-help-intro">
          Chain on the left, ticket on the right. Click strikes in the chain to set the legs, or let the auto-pick
          place them; the ticket prices the package and the risk chart draws it. Nothing is sent until you press the
          button at the bottom of the ticket. Descriptive, not advice: every number is a quote or a model value, and the
          decision is yours.
        </p>

        <h3>Header</h3>
        <dl>
          <dt>PAPER / LIVE / SIMULATION</dt>
          <dd>
            Which account this widget trades, from the switch at the top of the page. Paper and Live are your Alpaca
            accounts; Simulation is the dashboard's own practice book with live prices and simulated fills. During a
            history replay the badge reads SIMULATION · REPLAY and every price is the replayed moment's.
          </dd>
          <dt>Chain · Open spreads · Idea</dt>
          <dd>
            The three views. Chain is the picker and ticket. Open spreads lists what the account holds, grouped into
            the structures they were opened as. Idea asks Claude for structures on this chain (not offered during a
            replay).
          </dd>
          <dt>BP · L3 · opra</dt>
          <dd>
            Options buying power, the account's options approval level (2 buys calls and puts outright, 3 is needed
            for every spread here) and the data feed.
          </dd>
          <dt>?</dt>
          <dd>This page.</dd>
        </dl>

        <h3>Expiry strip</h3>
        <dl>
          <dt>4 Sep 0d, 8 Sep 4d, …</dt>
          <dd>
            Every listed expiration within the next ~60 days, with days to expiry. Click one to load its chain; [ and
            ] step through them. 0d is a contract expiring today: no greeks from the feed, fastest time decay.
          </dd>
        </dl>

        <h3>Chain</h3>
        <dl>
          <dt>Strike</dt>
          <dd>
            The middle column. Calls to the left, puts to the right. The tinted row is the spot; the green and red rows
            are the ticket's current legs (green bought, red sold).
          </dd>
          <dt>Bid · Mid · Ask</dt>
          <dd>
            The market for that contract. Mid is halfway; the ask is what a buyer pays at once, the bid what a seller
            receives at once. In a replay these are Bid* and Ask*: derived from the last print, because no historical
            quotes exist.
          </dd>
          <dt>Δ / Γ / Θ</dt>
          <dd>
            One greek column per side; click its header to cycle. Delta: how much the option moves per 1 $ move of
            the underlying, and roughly the market's odds of finishing in the money (0.50 at the money, 0.20 far out,
            puts negative). Gamma: how much delta changes per 1 $, so how fast a position turns near the money and
            near expiry. Theta: value lost per day at a standing price, per share, negative for a bought option. The
            cell tooltip always shows all three.
          </dd>
          <dt>IV</dt>
          <dd>
            Implied volatility, the annualised move the price implies. Higher IV means dearer options. Compare across
            strikes (skew) and expiries (term structure) rather than reading one number alone.
          </dd>
          <dt>OI</dt>
          <dd>
            Open interest, the contracts outstanding. A liquidity hint: thin OI usually means a wide spread and an
            awkward fill. Shows — in a replay (the historical figure would be look-ahead).
          </dd>
          <dt>Clicking a strike</dt>
          <dd>
            Sets the leg the strategy expects there: for a vertical the short leg, then the long one at Width strikes
            away; for a straddle both legs at that strike; for a condor the short put or call side depending on which
            side of the spot you clicked. For a calendar or diagonal, the Short/Long toggle in the ticket says which
            expiry the click is for.
          </dd>
        </dl>

        <h3>Strategy row</h3>
        <dl>
          <dt>Long</dt>
          <dd>
            Long call, Long put: one bought contract. Straddle: bought call and put at one strike. Strangle: bought
            call above and put below the spot. All pay a debit, all gain from a large move.
          </dd>
          <dt>Vertical</dt>
          <dd>
            Two legs, same expiry, same kind. Bull call and Bear put pay a debit and need a move. Bull put and Bear call
            receive a credit and need the underlying to stay beyond the short strike. Hotkeys 5 to 9 select these and
            the iron condor.
          </dd>
          <dt>Neutral</dt>
          <dd>
            Iron condor: a bull put and a bear call together, credit, profits inside the two short strikes. Iron fly:
            the same with both short legs at one strike. Call fly and Put fly: one kind, bought wings around a sold
            body (×2), cheap, profits near the body at expiry.
          </dd>
          <dt>Time</dt>
          <dd>
            Calendar: sell the near expiry, buy the same strike further out. Diagonal: the same with different
            strikes. Both live on the near leg decaying faster than the far one. The ticket shows both expiries.
          </dd>
          <dt>Income</dt>
          <dd>
            Covered call: sell a call against 100 shares you hold. Cash-sec. put: sell a put with the strike's cash
            set aside. The ticket checks the shares or the cash and says what is missing.
          </dd>
        </dl>

        <h3>Ticket controls</h3>
        <dl>
          <dt>Width / Wings</dt>
          <dd>
            Strikes between the long and the short leg (for butterflies: between body and each wing) when the
            auto-pick places the legs. In strikes, not dollars: Width 2 on SPY is 2 $, on a 5 $-strike stock 10 $.
            + and − change it.
          </dd>
          <dt>Short Δ / Strikes</dt>
          <dd>
            How far out the auto-pick puts the short leg(s). Δ mode aims for a delta (0.30 for a vertical, 0.20 for
            a condor by default; smaller is further out, less credit, more room). Strikes mode counts strikes from the
            spot instead: 0 is the first strike outside it. Shift+/− step it. Remembered per strategy group.
          </dd>
          <dt>Auto-pick</dt>
          <dd>Throws away your clicked strikes and places the legs by Width and Short again.</dd>
          <dt>corridor</dt>
          <dd>
            For a condor or strangle: the distance between the short put and the short call, the range the underlying
            has to stay in.
          </dd>
          <dt>Spreads / Contracts</dt>
          <dd>Quantity. One spread is one of each leg; the summary line multiplies by 100 and by this number.</dd>
          <dt>Min credit · Max debit · Max premium</dt>
          <dd>
            The limit price per spread, named by direction: the least you will accept for a credit package, the most
            you will pay for a debit one, the most for a single long. Prefilled from Mid or Natural; type your own and
            Reset returns to the prefill.
          </dd>
          <dt>Mid / Natural</dt>
          <dd>
            Which price the limit is prefilled with. Mid is the package's midpoint: a better price that may rest
            unfilled, especially on paper. Natural crosses the bid/ask on every leg: the worst price, fills at once.
            The choice is remembered. Greyed while there is no preview to read a price from.
          </dd>
          <dt>Receive / Pay … × 100 × n</dt>
          <dd>
            The summary: net premium per spread, times the multiplier, times quantity. Below it the mid, the natural,
            the spot and the days to expiry.
          </dd>
          <dt>Max profit · Max loss · Breakeven</dt>
          <dd>
            At expiry, per position. Unlimited or unbounded means the grid has no edge in that direction (a long call
            keeps rising with the underlying). The breakeven is where the expiry curve crosses zero.
          </dd>
          <dt>Collateral · ceilings</dt>
          <dd>
            What the account reserves for the package (a credit spread's width less the credit, the strike's cash for
            a cash-secured put), against the options buying power, plus the widget's own limits on contracts per
            order and notional per order.
          </dd>
          <dt>Legs list</dt>
          <dd>
            Each leg with its price, delta and side. Click one to load that contract's premium chart in the chart
            widget. In a replay a faded leg means a stale print.
          </dd>
          <dt>Place / Sell … on SPY</dt>
          <dd>
            Sends the package as one multi-leg limit order. On Live a dialog asks you to type LIVE first. In
            Simulation it fills against the practice book at the natural, or rests as a working package.
          </dd>
        </dl>

        <h3>Risk chart</h3>
        <dl>
          <dt>at expiry (solid)</dt>
          <dd>P&amp;L over the underlying's price when the (first) expiry arrives. Green and red tint the two sides of zero.</dd>
          <dt>today (dashed)</dt>
          <dd>
            The same legs valued now by Black-Scholes with each leg's IV. What the position would be worth if the
            underlying jumped there this instant.
          </dd>
          <dt>Time slider</dt>
          <dd>
            Moves the model's clock forward by hours (whole hours up to three days, then quarter days) and draws a
            dotted line. Shows the time value that leaves before your move arrives: on a 0DTE the afternoon alone
            can take most of it.
          </dd>
          <dt>IV slider</dt>
          <dd>
            Scales every leg's implied volatility, ±50 %. A vol drop after the open or a data release takes value from
            long premium even when the underlying goes your way; credit structures gain from it.
          </dd>
          <dt>Hover</dt>
          <dd>The readout gives the three values at the hovered price. Drag the bottom edge to resize the chart.</dd>
          <dt>Clock</dt>
          <dd>
            The slider's target time, the chain's last-print times and every other clock in the app are shown in the
            zone chosen under Settings → Display → Time zone: your browser's, or New York's.
          </dd>
          <dt>Model caveats</dt>
          <dd>
            Constant IV per leg, no skew, no dividends, no rate. The curves show direction and size, not the fill you
            will get.
          </dd>
        </dl>

        <h3>Open spreads</h3>
        <dl>
          <dt>Rows</dt>
          <dd>
            Held contracts grouped into the structures they were opened as (an iron condor and a separate long put on
            the same expiry stay two rows). Each with entry, mark, P&amp;L and the underlying.
          </dd>
          <dt>Mark</dt>
          <dd>The position's own price now: the mid of closing it, per share.</dd>
          <dt>Close</dt>
          <dd>Previews the closing package at the current market, then sends it as one order (typed LIVE on Live).</dd>
          <dt>Triggers</dt>
          <dd>
            Server-side rules that close a position when the underlying crosses a price or the position's own premium
            crosses a level (a stop or a take-profit). Not broker orders: they fire only while the backend runs.
          </dd>
        </dl>

        <h3>Levels the chart draws from this chain</h3>
        <dl>
          <dt>Call Wall · Put Wall · Flip</dt>
          <dd>
            Dealer gamma exposure (GEX) summed over every expiry in the next 45 days, ±15 % around spot: open
            interest × gamma per strike, calls positive, puts negative, under the usual assumption that dealers are
            short the calls and long the puts. The Call Wall is the strike with the most positive gamma (their
            hedging sells into a rise there, so it acts as resistance), the Put Wall the most negative (support), the
            Flip the price where net gamma changes sign: above it dealers dampen moves, below it they amplify them.
            Month-scale positioning, moves slowly. The GEX Plan widget spells out the regime.
          </dd>
          <dt>0DTE Call Wall · 0DTE Put Wall · 0DTE Flip</dt>
          <dd>
            The same profile for the nearest expiry alone, today's while it still trades (after the close, or on a
            weekend, the next one, tagged with its days: "3d Flip"). Per contract a same-day option carries many
            times the gamma of a monthly, so these are the walls that matter for the day, and they shift as the day
            goes on. Alpaca computes no greeks for a contract expiring today, so 0DTE gamma is solved here from each
            contract's own quote, the same Black-Scholes solver the replayed chain uses; the GEX Plan says "gamma
            solved from quotes" when that happened. Open interest is last night's, so positions opened today are not
            in it yet, which understates a busy 0DTE strike.
          </dd>
          <dt>EM + / EM −</dt>
          <dd>
            The expected move to the nearest expiry, read off the at-the-money straddle: call mid plus put mid at the
            strike nearest spot. Under Black-Scholes the straddle's price <em>is</em> the market's expected absolute
            move, so the band is spot ± straddle; one sigma (68 % of outcomes) is the straddle × 1.25 and appears in
            the badge's tooltip and in the GEX Plan. Symmetric by construction, skew is ignored. Use it to judge a
            breakeven: a long option whose breakeven lies outside the band needs a bigger day than the market is
            pricing.
          </dd>
          <dt>Levels menu</dt>
          <dd>
            Each set ("GEX", "Near GEX", "EM band") is a separate entry in the chart's Levels checklist and can
            be shown or hidden on its own.
          </dd>
        </dl>

        <h3>Idea</h3>
        <dl>
          <dt>Suggest structures</dt>
          <dd>
            Claude reads three expiries of this chain plus GEX, news, implied vol and the next earnings date and
            proposes up to three structures. The server snaps them onto listed strikes and prices them through the
            same path as the ticket, so the cards' numbers are the options stack's, not the model's. Takes minutes.
          </dd>
          <dt>Support n/10</dt>
          <dd>
            How well the available data backs that structure over the alternatives. Not a probability of profit.
          </dd>
          <dt>Load into ticket</dt>
          <dd>Puts the structure's legs and expiry into the ticket; you still price and submit it yourself.</dd>
        </dl>
      </div>
    </Modal>
  );
}
