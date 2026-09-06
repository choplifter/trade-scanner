import { Modal } from "../common/Modal";

interface OptionsHelpProps {
  open: boolean;
  onClose: () => void;
}

/** Every term on the Options widget, in the order the eye meets them:
 * header, expiry axis, chain, ticket, risk chart, Open spreads, Idea. A
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
          <dt>Chain · Open spreads · Idea · Optimizer</dt>
          <dd>
            The four views. Chain is the picker and ticket. Open spreads lists what the account holds, grouped into
            the structures they were opened as. Idea asks Claude for structures on this chain (not offered during a
            replay). Optimizer searches the chain for the structures that pay best at a price target you name.
          </dd>
          <dt>BP · L3 · opra</dt>
          <dd>
            Options buying power, the account's options approval level (2 buys calls and puts outright, 3 is needed
            for every spread here) and the data feed.
          </dd>
          <dt>?</dt>
          <dd>This page.</dd>
        </dl>

        <h3>Expiry axis</h3>
        <dl>
          <dt>Sep · Oct · … · Jan '27 · … · Dec '28</dt>
          <dd>
            The whole board on one line: a band of months, the listed days beneath as chips, out to the LEAPS two
            or three years off. The pressed chip is the ticket's expiry; click another to load its chain, [ and ]
            step through them, and the head shows its days to expiry. A chip expiring today has no greeks from the
            feed and the fastest time decay. Hover a chip for the weekday, the year and the contract count. When
            the axis is wider than the panel, drag it sideways or roll the wheel over it.
          </dd>
          <dt>Two legs</dt>
          <dd>
            A calendar or diagonal marks its long leg's expiry in purple next to the short leg's blue, and the head
            shows both (33d · 75d). A click moves the leg being picked (the one whose strike you last clicked on
            the rail); ⇧-click moves the other. The far expiries' quotes are fetched when one is picked, so the
            first click on a LEAPS date takes a moment longer.
          </dd>
        </dl>

        <h3>Events on the axis</h3>
        <dl>
          <dt>E · FOMC · CPI …</dt>
          <dd>
            A red dot under a chip marks the first expiry held through the next earnings report, a purple dot the
            first expiry held through a scheduled US release (FOMC decision, CPI, payrolls, PCE, GDP); hover the
            chip to read which. Every later expiry is held through the same event; the mark sits where it enters
            the axis. Dates come from FMP; without a key nothing is marked, which means "not known", not "nothing
            scheduled".
          </dd>
          <dt>The line below</dt>
          <dd>
            The report date with its days away and the stock's <em>typical move</em> over its past reports: the
            median of the close-to-close move from the close before each report date to the first close after it
            (two sessions, so a before-the-open and an after-the-close report are measured alike), over up to eight
            reports, with the largest. Then the macro dates, then the <strong>IV rank</strong>: where today's
            at-the-money IV sits between the lowest and highest this app has recorded for the symbol over the past
            year of sessions — green above 60 % (premium rich against its own history), amber between, blue below
            30 % (cheap). It needs twenty recorded sessions; the Idea tab and this line record one per day.
          </dd>
        </dl>

        <h3>Strike rail</h3>
        <dl>
          <dt>Ticks · spot</dt>
          <dd>
            The rail under the expiry axis is the chain's price axis: one tick per listed strike, the ends labelled,
            the spot marked in amber. It shows the expiry the chain shows.
          </dd>
          <dt>Handles</dt>
          <dd>
            One per leg of the current strategy, coloured like the chain's cells: green bought, red sold, the fly's
            body outlined. Hover one for its name and strike. Drag it and it snaps to the nearest strike quoted for
            that leg's kind; the ticket reprices as it moves. A leg dragged into another pushes that one a strike
            out of the way rather than crossing it, the same rule a chain click follows.
          </dd>
          <dt>Shift-drag · arrows</dt>
          <dd>
            Hold Shift while dragging and every leg moves together by whole strikes, widths kept; the move stops at
            the chain's edge rather than bunching the legs. With a handle focused, ← → nudge it one strike, Shift + ←
            → nudge all of them. Like a click, a drag switches the auto-pick off until the strategy or expiry
            changes.
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

        <h3>Strategies</h3>
        <p className="options-help-intro">
          Sixteen shapes in five groups. For each: what you hold, whether you pay or receive, where it makes and
          loses money at expiry, what it is usually for, and what to watch. "Level" is the Alpaca options approval
          the account needs; Paper accounts have level 3. The auto-pick places the legs from Width and Short (see
          Ticket controls); every leg can be moved by clicking the chain. None of this is advice: it describes the
          shape, not whether to trade it.
        </p>

        <h3>Long (level 2)</h3>
        <dl>
          <dt>Long call</dt>
          <dd>
            Buy one call. Pay the premium; that is the most you can lose. Profits above strike + premium, without
            limit. The directional bet with time working against you: a 0DTE at-the-money call loses most of its
            value in the last two hours if the underlying stands still, so the Time slider under the risk chart
            is worth a look before buying one. Delta 0.40 to 0.55 moves nearly one-for-one with the underlying;
            cheaper 0.20 calls need a larger move before they respond.
          </dd>
          <dt>Long put</dt>
          <dd>
            Buy one put. The mirror: profits below strike − premium, loses at most the premium. Also the classic
            hedge for shares. Puts on SPY usually carry a higher IV than calls the same distance away (the skew),
            so the same distance costs more on the put side.
          </dd>
          <dt>Straddle</dt>
          <dd>
            Buy a call and a put at the same strike, at the money. Pay both premiums. Profits when the underlying
            moves further than the total premium in either direction; loses most when it stays put. Its price is
            the market's expected move to expiry (see the EM lines on the chart), so buying one is a bet the
            realised move beats what is priced. Bought before an event, sold after it: implied vol collapses once
            the news is out, and a straddle bought at high IV can lose even when the underlying moves.
          </dd>
          <dt>Strangle</dt>
          <dd>
            Buy a call above and a put below the spot. Cheaper than the straddle, needs a bigger move before either
            leg is in the money. Same logic, wider breakevens, lower cost. Short Δ sets how far out the auto-pick
            puts the two legs (0.25 by default); the ticket shows the corridor between them.
          </dd>
        </dl>

        <h3>Vertical (level 3)</h3>
        <dl>
          <dt>Bull call</dt>
          <dd>
            Buy a call, sell a higher call, same expiry. Pay a debit. Max profit is the width minus the debit,
            reached at or above the short strike; max loss is the debit, below the long strike. A cheaper, capped
            version of the long call: the sold leg pays for part of the bought one and caps the upside. Width in
            strikes sets how much of each. Breakeven is the long strike plus the debit.
          </dd>
          <dt>Bear put</dt>
          <dd>
            Buy a put, sell a lower put. The mirror of the bull call for a move down: debit, capped profit at the
            width minus the debit below the short strike, loses the debit above the long strike.
          </dd>
          <dt>Bull put</dt>
          <dd>
            Sell a put, buy a lower put. Receive a credit and keep it if the underlying stays above the short
            strike at expiry. Max loss is the width minus the credit, below the long strike. The income version
            of "I don't think it falls through here": the short strike is placed at a level you expect to hold
            (a put wall, a prior low), the long strike caps the damage. Short Δ (0.30 by default) sets how far
            out the short leg goes: smaller delta, further out, less credit, more room. The account reserves the
            width minus the credit as collateral.
          </dd>
          <dt>Bear call</dt>
          <dd>
            Sell a call, buy a higher call. The mirror: credit kept if the underlying stays below the short strike,
            max loss width minus credit above the long strike. Placed above a call wall or a session high it is
            the bet the rally stalls there.
          </dd>
        </dl>

        <h3>Neutral (level 3)</h3>
        <dl>
          <dt>Iron condor</dt>
          <dd>
            A bull put and a bear call together: sell a put below and a call above the spot, buy a further put and
            call as wings. Receive both credits. Profits when the underlying stays between the two short strikes
            at expiry; the maximum loss is one wing's width minus the total credit, on whichever side breaks.
            The corridor shown in the ticket is the range that has to hold. Short Δ (0.20 by default) sets the
            short strikes' distance, Width the wings. Positive-gamma days (see the GEX regime) suit it; a
            negative-gamma regime is where a condor gets run over.
          </dd>
          <dt>Iron fly</dt>
          <dd>
            An iron condor with both short legs at one strike, at the money: sell the call and the put there, buy
            a call above and a put below as wings. Largest credit of the neutral shapes, narrowest sweet spot:
            maximum profit only if the underlying closes exactly at the body, breakevens at the body ± the
            credit. A bet on pinning at a strike, typically near a gamma wall on expiry day. Closing it buys the
            body back and sells the wings; in Simulation the Close dialog suggests the natural so it fills.
          </dd>
          <dt>Call fly</dt>
          <dd>
            Buy a call below, sell two calls at the body, buy a call above, all one kind and one expiry. Pay a
            small debit; that is the maximum loss. Maximum profit at the body at expiry: the wing width minus the
            debit, often several times the debit. Cheap because it needs the underlying near the body at the
            end, and worth little until close to expiry. Wings sets the distance from body to each wing.
          </dd>
          <dt>Put fly</dt>
          <dd>The same shape in puts. Equivalent payoff; pick whichever side is quoted tighter or where the debit is smaller.</dd>
        </dl>

        <h3>Time (level 3)</h3>
        <dl>
          <dt>Calendar</dt>
          <dd>
            Sell the near expiry, buy the same strike in a later expiry, one kind. Pay a debit (the far option
            costs more). Profits if the underlying sits near the strike as the near leg expires, because the
            near option decays faster than the far one; loses if it runs far away in either direction, or if the
            far expiry's IV falls. The ticket's expiry is the sold leg's; Long expiry picks the bought one, and
            the Short/Long toggle says which expiry a chain click sets. Useful when the front expiry's IV stands
            well above the back's (an event priced into one week).
          </dd>
          <dt>Diagonal</dt>
          <dd>
            A calendar with different strikes: sell a nearer, further-out-of-the-money option, buy a later,
            closer one. Adds a directional lean to the time decay. The "poor man's covered call" is a diagonal:
            a long-dated deep call instead of shares, a short near call sold against it.
          </dd>
        </dl>

        <h3>Income (level 1)</h3>
        <dl>
          <dt>Covered call</dt>
          <dd>
            Sell a call against 100 shares you hold per contract. Receive the premium; if the underlying finishes
            above the strike the shares are called away at the strike, so the upside is capped there plus the
            premium. Downside is the shares' own. The ticket checks the shares are in the account (in Simulation,
            in the practice book) and says what is missing.
          </dd>
          <dt>Cash-sec. put</dt>
          <dd>
            Sell a put with the strike's cash set aside. Receive the premium; if the underlying finishes below the
            strike you buy 100 shares per contract at the strike, effectively at strike − premium. A way to be
            paid for a limit buy order. The account reserves the strike × 100 as collateral.
          </dd>
        </dl>

        <h3>Reading a shape quickly</h3>
        <dl>
          <dt>Debit or credit</dt>
          <dd>
            Debit shapes (long options, bull call, bear put, flies, calendars) pay first and need something to
            happen; time and falling IV work against them. Credit shapes (bull put, bear call, condor, iron fly,
            income) are paid first and need nothing to happen; time and falling IV work for them, a large move
            against them.
          </dd>
          <dt>Where the money is made</dt>
          <dd>
            The risk chart's solid line is the answer at expiry; hover it. The dashed line is today. If the two
            differ a lot at the price you expect, the position needs time, not just direction.
          </dd>
          <dt>Hotkeys</dt>
          <dd>5 to 9 select bull call, bear put, bull put, bear call and iron condor; + and − change Width; [ and ] step the expiry.</dd>
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
          <dt>Chart · Table</dt>
          <dd>
            The same position as a table: rows are prices around the spot (the spot's own row marked), columns the
            trading days from now to expiry, each cell the model P/L there with the IV slider applied, coloured on
            the heatmap scale. Columns are closes (16:00 New York, shown in your zone); weekends are skipped,
            exchange holidays are not, so a holiday reads as one more day of decay. The $ / % of risk switch relates
            each cell to the position's defined maximum loss where it has one. The Time slider hides in table view,
            since the columns are its axis.
          </dd>
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
          <dt>Roll…</dt>
          <dd>
            On a single short leg (a cash-secured put, the call of a covered call): close it and open its replacement
            on another expiry or strike as one ticket. The new strike defaults to the same strike while it is still
            out of the money, else the strike nearest 0.30 delta on the new expiry; the net per package is what the
            roll pays or brings, and the new leg is judged with the old leg's collateral already released, so a put
            rolled to the same strike needs no new cash. In Simulation the book fills the roll as one package — both
            legs or neither — at the natural unless a net limit is typed; at Alpaca it is two orders in sequence, and
            the result says if the second was refused (coverage the broker frees only once the close fills, a limit
            the market has left).
          </dd>
          <dt>Triggers</dt>
          <dd>
            Server-side rules that close a position when the underlying crosses a price or the position's own premium
            crosses a level (a stop or a take-profit). Not broker orders: they fire only while the backend runs.
          </dd>
        </dl>

        <h3>Optimizer</h3>
        <dl>
          <dt>Outlook</dt>
          <dd>
            OptionStrat's six views, each a shortcut for a target and a set of shapes. The unit is the{" "}
            <em>implied move</em>: one standard deviation of the move the option market prices to the chosen
            expiry, spot × ATM IV × √time, shown next to the inputs. Bullish sets the target one implied move above
            the spot, Very bullish two; Bearish and Very bearish mirror it; Neutral leaves the target at the spot and
            searches condors, flies and calendars; Directional expects a move either way and ranks straddles and
            strangles at both points at once. The target and the families stay editable afterwards.
          </dd>
          <dt>Target · Expiry</dt>
          <dd>
            Where you expect the underlying and when. The percentage beside the target is against the spot. The
            expiry chips are the listed expiries grouped by month; one expiring today is not offered, because a
            contract without time left has no implied volatility to price a horizon on. "More options" adds a max
            loss and the family checkboxes.
          </dd>
          <dt>Events · hold through earnings</dt>
          <dd>
            Under the chips, the same events line as the chain's, with a switch. <strong>Hold through earnings</strong>{" "}
            off leaves out every expiry on or after the report (the chips grey out, a horizon on or after it moves
            back to the last one before it) so no structure is held through the print; on, structures may span it
            and the results carry a warning that the IV in their prices includes the report and will not survive it.
            With earnings inside the horizon, a note compares what the market prices <em>for the report itself</em>{" "}
            with what the stock actually moved over its past reports. The report's implied move is read off two
            expiries: the horizon expiry's implied variance (ATM IV² × time) less the last pre-report expiry's — the
            ordinary days cancel, what is left is the print — as a percentage of spot. When the stock has usually
            moved more than that, buying the move (Directional: straddle, strangle) has been the cheaper side; when
            less, selling it (Neutral: condor, iron fly). The button applies that view. The numbers are a record,
            not a forecast; the past moves span two sessions, the implied one is a one-sigma figure.
          </dd>
          <dt>IV rank light</dt>
          <dd>
            Beside the implied move: green means premium is rich against the symbol's own history (rank above 60 %)
            and offers <em>credit shapes</em> — bull put, bear call, iron condor, iron fly; blue means cheap (below
            30 %) and offers <em>debit shapes</em> — long call and put, debit spreads, straddle, strangle, calendar.
            The buttons only tick the families; the search still runs on your target.
          </dd>
          <dt>From the scanner</dt>
          <dd>
            Every scanner row has an <strong>Opt</strong> button: it selects the symbol, opens this tab and runs the
            view the day's move suggests — Bullish for an up move, Bearish for a down move, "Very" beyond ten
            percent — on the first expiry. The results say "From the scanner (move +12.4 %)" so the view is not
            mistaken for one you chose.
          </dd>
          <dt>Max Return ↔ Max Chance</dt>
          <dd>
            Where the ranking stands between the two numbers on the cards. At the left, structures are ordered by
            return on risk (what they pay if the target is reached); at the right by chance of profit (how likely
            any profit is at all). In between, each structure's percentile on both scales is blended. Far
            out-of-the-money shapes win the left end, wide credit structures the right.
          </dd>
          <dt>Budget · Max loss</dt>
          <dd>
            Per position, in dollars. Budget caps what the account puts up: the debit of a bought structure, the
            collateral of a credit one. Max loss caps the defined maximum loss; a structure whose loss is unbounded
            never passes it.
          </dd>
          <dt>Families</dt>
          <dd>
            Which shapes to search. Income shapes start unticked because they need shares or cash the optimizer
            cannot see; diagonals are not searched at all (the ticket builds one by hand in a moment).
          </dd>
          <dt>What it does</dt>
          <dd>
            Loads the horizon's expiry and two later ones, keeps the strikes that are listed, tradable and quoted on
            both sides, and enumerates every shape within sensible bounds: verticals up to three strikes wide,
            condors with short deltas 0.10–0.40 and equal wings, flies around the target, straddles and strangles,
            calendars where a later expiry is loaded — about a thousand candidates on a dense chain. Each is priced
            from the mids, its risk taken from the same closed forms the ticket shows, its P/L at every point of the
            target on the horizon date computed with the risk chart's own Black-Scholes, each leg's IV held still.
            The dozen best then go through the ticket's own preview, and the cards show that preview's numbers.
          </dd>
          <dt>Return on risk · Profit · Risk</dt>
          <dd>
            Return on risk is the P/L at the worst point of the target divided by what the account puts up — "if the
            target is reached, at least this many percent of the risk". Profit is that P/L in dollars, Risk the
            debit or collateral. A ranking of payoff if you are right, at one price and date with IV unchanged. The
            colours are relative to the list: the best value is green, within half of it amber, below that grey.
          </dd>
          <dt>Chance</dt>
          <dd>
            The probability of <em>any</em> profit on the horizon date if the underlying moves as the option market
            itself prices it: a lognormal distribution at the chain's at-the-money IV, no drift. The P/L curve is
            integrated over that distribution (each leg's IV held still). A model number, not a forecast — it says
            how much of the market's own distribution the structure covers, which is why a wide credit spread scores
            high and a far out-of-the-money call low, whatever you think of the target.
          </dd>
          <dt>The small chart</dt>
          <dd>
            Each card's P/L at expiry over the price grid: green where the position ends in profit, red where in
            loss, breakevens dotted with their prices, the target amber, the spot blue. The same curve the ticket's
            risk chart draws once the structure is loaded.
          </dd>
          <dt>The line below the cards</dt>
          <dd>
            Every shape that was enumerated is either on a card or counted there with its reason: over budget, under
            $5 of risk (a structure whose mids net to a cent is a quote artefact — it would show a thousand-percent
            return on one dollar), lose at the target, no market, no IV, quoted the wrong way, mispriced, or beyond
            the candidate cap. "Nothing
            reaches this target" and "everything was over budget" are different answers. Finalists the account
            refuses (an options level, a ceiling) are listed with the refusal.
          </dd>
          <dt>In a replay</dt>
          <dd>
            Offered, unlike Idea: nothing enters the ranking but the chain the ticket itself is priced from at the
            replayed moment. That chain is synthetic — bid/ask derived from the last print, IV solved back out of it
            — and a warning above the cards says so.
          </dd>
        </dl>

        <h3>Playbooks (Simulation · Paper)</h3>
        <dl>
          <dt>What a playbook is</dt>
          <dd>
            A script that runs an options <em>campaign</em> over weeks — the Wheel first: sell a cash-secured put; when
            assigned, hold the shares and sell covered calls above the cost basis; when called away, start again. The
            Poor Man's Wheel second (below). Like the indicator and strategy scripts it is a file
            (backend/app/playbooks/), but stateful and event-driven: it reads the campaign's phase, shares, cost basis,
            open legs (short and long), the chains around its DTE windows and the calendar, and answers with one action
            — sell a put, sell a call, buy a call, roll, close, or hold with a reason.
          </dd>
          <dt>Propose, not trade</dt>
          <dd>
            The runner refreshes each campaign's <strong>next step</strong> every few minutes and after every fill,
            expiry or assignment. You place it: "Load into ticket" prefills the Chain tab's ticket, "Open roll ticket"
            the roll (the roll ticket also rolls one leg of a diagonal, and a long call into a long call), "Close…"
            prices the close and places it at the natural on confirmation. Only the Simulation account offers <strong>auto-execute</strong> (tick twice to confirm): the
            runner then places proposals itself during the regular session, and switches itself off after a failure.
          </dd>
          <dt>Paper account</dt>
          <dd>
            In Paper mode a campaign reads your Alpaca paper account: filled option orders become the campaign's
            events (a sold put, a covered call, a close, a roll placed as one multi-leg order), and the settlements
            Alpaca reports not as orders but as <em>account activities</em> — assignment, expiration, exercise — are
            read from there, so a put assigned over the weekend shows as "Assigned" with the shares at the strike on
            Saturday morning. The same activities close the contract's round trip in the Trading Journal at 0.00
            (its trip is the premium kept; the shares carry the rest), which Alpaca's order history alone never did.
            A paper campaign never executes anything, whatever the switch says; Live is not offered.
          </dd>
          <dt>Phase · basis · premiums</dt>
          <dd>
            Phase is read from the books: cash (nothing held), short put, assigned (shares, no call), covered call,
            mixed. <strong>Cost basis</strong> = (average entry × shares − premiums collected) / shares — an
            assignment enters the shares at the strike, every credit since the campaign started lowers it, and a call
            is never proposed below it (switchable). Premiums count every option credit less every debit since the
            start; realized P&amp;L adds the shares' round trips (assigned at the put strike, called away at the call
            strike). Shares you buy or sell by hand are noted, not fought: the phase simply moves.
          </dd>
          <dt>Wheel parameters</dt>
          <dd>
            Contracts; put and call delta; the DTE window to sell into; roll at DTE and take-profit % (a leg is rolled
            to the next expiry in the window once it has earned that share of its credit or has that few days left —
            same strike while out of the money, else the delta strike); avoid earnings (no expiry on or after the
            next report); a collateral budget per put; call ≥ basis; <strong>accept assignment</strong> (on by default:
            an in-the-money leg near expiry is left to be assigned or called away — the wheel turning — rather than
            rolled; off rolls it out).
          </dd>
          <dt>Poor Man's Wheel</dt>
          <dd>
            The wheel's call side with a long-dated, deep in-the-money call (a LEAPS) in place of the 100 shares: the
            campaign buys the call at the <strong>LEAPS delta</strong> (0.80) in the LEAPS window (270–540 days), then
            sells shorter calls against it at the call delta, before the LEAPS expires. The short call is rolled at the
            take-profit % or near expiry as in the wheel; <strong>in the money near expiry</strong> the wheel turns
            instead (switchable, "Turn when ITM"): both legs are closed as one package — the long call has taken the
            move the short call gave away — and a fresh long call is bought. The long call itself is rolled out with
            <strong>Roll LEAPS at DTE</strong> days or fewer left, or down when its delta has fallen below
            <strong>LEAPS min delta</strong> (the stock dropped). Cost basis here is the long call's strike plus its
            debit less the premiums collected, per share — the same question as the wheel's, and the same floor for the
            short strike. The short call is a covered call whose <em>cover</em> is the long call (a strike at or below,
            an expiry no earlier); the ticket preview says "cover" rather than "shares". Phases: <em>LEAPS</em>
            (the long call alone), <em>LEAPS + call</em>. The long side's buys and sells are counted apart from the
            premiums; realized P&amp;L adds them. Capital tied up is the call's debit, not the shares' price — and a
            long call, unlike shares, decays and expires.
          </dd>
          <dt>From the Positions tab</dt>
          <dd>
            "Wheel…" on a share lot of 100 or more (Simulation) opens this tab with the start form for that symbol:
            the campaign adopts the shares and proposes the first covered call. The Poor Man's Wheel starts from cash:
            pick it in the script menu next to the start button.
          </dd>
          <dt>Backtest (synthetic)</dt>
          <dd>
            Walks the playbook over up to 24 months of the symbol's daily closes. There are no historical option
            prices in this app, so each day's chain is <em>built</em>: Black-Scholes on the trailing 20-session
            realized volatility times an IV-premium factor (1.15 by default — options usually trade above what the
            stock then realizes), one flat sigma per day, weekly and monthly Fridays, strikes on the exchange grid, a
            bid/ask of a fixed fraction around the mid, fills at the bid to sell and the ask to buy back, expiries
            settled by the same table as the simulated book (assignment into shares, calls called away). The equity
            line is shown against buying the shares outright. It tells you how the rules <em>behave</em> — how often
            a put is assigned, how many rolls, how long the shares are held, what the premiums add up to — not what
            they would have earned: no skew, no dividends, no early assignment, European exercise.
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
