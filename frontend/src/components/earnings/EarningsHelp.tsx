import { Modal } from "../common/Modal";

interface EarningsHelpProps {
  open: boolean;
  onClose: () => void;
}

/** Every column and every term on the Earnings widget, in the order the
 * eye meets them: the table, then the expanded row's signals, scores and
 * cards. The rules it describes live in backend
 * app/options/strategy_select.py and app/options/earnings_evaluate.py;
 * keep the three in step when a threshold changes. */
export function EarningsHelp({ open, onClose }: EarningsHelpProps) {
  return (
    <Modal open={open} title="The Earnings screen" onClose={onClose} className="modal-panel-wide">
      <div className="options-help">
        <p className="options-help-intro">
          Who reports today and on the next session, sorted by how liquid they are, and — when you open a row — which
          option structures the data argues for. The list is cheap and loads at once; opening a row reads two chains,
          the gamma profile and prices up to a dozen structures, so it takes a few seconds. Descriptive, not advice:
          a score counts signals, it does not predict anything, and nothing is ordered from here.
        </p>

        <h3>How to use it</h3>
        <dl>
          <dt>1. Scan the Ratio column</dt>
          <dd>
            It compares what the market charges for this print with what the stock has actually done over its past
            eight reports. Green means the market charges more, amber means less. That comparison is the whole point
            of the screen; everything else is context for it.
          </dd>
          <dt>2. Open a row</dt>
          <dd>
            Click it. The row selects the symbol across the dashboard (chart, chain, ticket all follow) and fetches
            its evaluation. Click again to close.
          </dd>
          <dt>3. Read the reasons, not the score</dt>
          <dd>
            Each family lists the sentences that produced its points, with the numbers in them. If you disagree with
            a sentence, the score built on it is worth nothing to you — that is the intended way to use it.
          </dd>
          <dt>4. Check what it costs to cross</dt>
          <dd>
            Every card says so, in cents per share. A structure whose market is wide fills only at a price well away
            from the numbers on the card, however good those look. This is the difference between a suggestion you
            can act on and one that rests all day.
          </dd>
          <dt>5. Load a card, or open the Optimizer</dt>
          <dd>
            <strong>Load into ticket</strong> plants the structure in the Options widget's ticket, where you check it
            and submit it by hand. Its <strong>Mid | Natural</strong> switch decides which price the limit starts at:
            Natural crosses the market and fills at once, Mid is the better price and may rest.{" "}
            <strong>Open optimizer</strong> hands a family this screen does not price over to the Optimizer tab with
            the horizon and family preset — note that those runs are not filtered for fillability the way this screen
            is.
          </dd>
        </dl>

        <h3>The table</h3>
        <dl>
          <dt>Symbol</dt>
          <dd>
            Reporters that Alpaca actually trades, above a trailing dollar-volume floor (the scanner's, 20 M by
            default). Names the calendar lists but Alpaca does not trade, and names too thin to have a usable chain,
            are left out; the count of what was dropped is in the empty state.
          </dd>
          <dt>When</dt>
          <dd>
            <strong>Today</strong> or <strong>Next</strong>, and a tick once the company has published. Both sessions
            are listed because a report after today's close moves tomorrow's session, and the calendar does not give
            the timing reliably. A ticked row's event premium has already been paid out — its Ratio will usually have
            collapsed below 1, which is the number telling you so.
          </dd>
          <dt>Last · Chg</dt>
          <dd>
            The live price and today's change when the scanner is tracking this symbol; otherwise the last completed
            close and a dash. A dash is "not being tracked", not "unchanged".
          </dd>
          <dt>Avg $vol</dt>
          <dd>Mean dollar volume over the last 20 completed sessions. The sort order by default.</dd>
          <dt>Strikes</dt>
          <dd>
            How many strikes the front expiry lists near the money. A coarse read on whether the chain can carry a
            structure at all: six is a chain where two legs are a stretch, forty is a busy board.
          </dd>
          <dt>Implied</dt>
          <dd>
            One standard deviation of the move the option market prices to the first expiry after the report, from
            its at-the-money implied volatility. On the report day this covers the ordinary sessions to expiry as
            well as the print — there is no pre-report expiry left to net the print out against.
          </dd>
          <dt>Past</dt>
          <dd>
            The median of the stock's own moves across its past reports: the close before each report date to the
            first close after it, two sessions, so a before-the-open and an after-the-close report are measured
            alike. Up to eight reports. Hover for why it is missing when it is.
          </dd>
          <dt>Ratio</dt>
          <dd>
            Implied divided by Past. Above 1.15 (green) the market charges more than the stock has historically
            delivered; below 0.85 (amber) less. Needs at least two past reports, or it stays blank rather than
            resting on one.
          </dd>
        </dl>

        <h3>The signals line</h3>
        <dl>
          <dt>IV vs realised</dt>
          <dd>
            At-the-money implied volatility over the stock's 20-day realised volatility. Unlike the IV rank it needs
            no stored history, so it is the volatility signal that works on a symbol's first day here.
          </dd>
          <dt>IV rank</dt>
          <dd>
            Where today's implied volatility sits between the lowest and highest this app has recorded for the symbol.
            It needs twenty recorded sessions; until then it reads "— (n sessions)". That is <em>unknown</em>, never
            "unremarkable" — no rule scores it while it is blank.
          </dd>
          <dt>Term</dt>
          <dd>
            The horizon expiry's at-the-money implied volatility over a later expiry's. Well above 1 means the near
            expiry carries the event premium the far one does not, which is the shape a calendar is built on.
          </dd>
          <dt>Skew</dt>
          <dd>
            Put implied volatility minus call implied volatility the same distance from spot. Positive is the ordinary
            equity shape (downside insurance costs more); it says which side is the richer one to sell.
          </dd>
          <dt>GEX</dt>
          <dd>
            Dealer gamma positioning. Net long gamma tends to damp moves and pin price toward large strikes; net short
            tends to amplify them. A blank means no reading was available, which is not the same as neutral
            positioning.
          </dd>
          <dt>Wings</dt>
          <dd>
            Quotable strikes beyond the expected move on the put and call side — what a four-legged structure needs
            to exist at all. Two limits make this zero more often than you might expect: the chain is fetched only
            within ±10 % of spot, and a strike counts only if it is two-sided, held by at least ten contracts and
            quoted no wider than a quarter of its own mid. Into a print those spreads blow out, so on many symbols
            there is genuinely nothing to buy as a wing.
          </dd>
        </dl>

        <h3>The scores</h3>
        <dl>
          <dt>+4 Bear call spread</dt>
          <dd>
            The sum of the rules that fired for that family: each adds one point, or two where the signal is the
            stronger kind, and the same rules subtract. So a +4 is four rules agreeing, and the long straddle beside
            it at −3 is three arguing against. It measures how many of the <em>available</em> signals point at a
            family and how strongly, relative to the others in this list. It is not a probability, not a forecast and
            not a recommendation — the chance of profit on each card is a separate number from the market's own
            implied distribution. A family with no rule to its name scores zero, which means "nothing here argues
            either way" rather than "bad idea", and those are left out of the list.
          </dd>
          <dt>The sentences beneath</dt>
          <dd>
            One per rule that fired, each naming the number that fired it. Absent data never produces a sentence,
            because a rule with a missing input is skipped rather than guessed at.
          </dd>
          <dt>Not priced here</dt>
          <dd>
            Two families are scored but never priced on this screen. A <strong>calendar</strong>'s edge is the front
            expiry's implied volatility collapsing on the report, and the pricing here deliberately values every leg
            at expiry, so it cannot see that edge. A <strong>directional</strong> family needs a view on which way the
            stock goes, which none of these signals supplies. Both carry an Open optimizer button instead.
          </dd>
        </dl>

        <h3>The priced cards</h3>
        <dl>
          <dt>The target</dt>
          <dd>
            Structures are judged over a range of <em>half</em> the stock's median past report move either side of
            spot, and ranked by their <em>worst</em> point in it — so one has to earn at both ends of that range, not
            merely somewhere inside it. Half, because judging one over the whole move only leaves structures whose
            short strikes sit beyond it, and beyond a typical earnings move a chain is thin, badly quoted or simply
            stops: measured on ORCL into its report, all 92 shapes were dropped at the full move and six survived at
            half of it. The full move is shown beside the target as context.
          </dd>
          <dt>0.06 to cross</dt>
          <dd>
            What taking this package costs against its own mid, per share. The card's numbers are the mid's, and the
            mid is a point nobody has to trade at; this says how far a limit there is from a fill, and what crossing
            gives up. Amber past a quarter of the package's price — past that the figures above it describe a fill
            you are unlikely to get. A structure whose market eats more than half its price is dropped outright and
            reported with the number.
          </dd>
          <dt>Dragging a card</dt>
          <dd>
            The card drags onto the chart as the underlying; each leg in its legs line drags as its own contract and
            lands as that contract's premium chart. Holding ⇧ while dragging the card does the same without aiming
            at a leg.
          </dd>
          <dt>Only one expiry</dt>
          <dd>
            Every leg expires on the horizon, so the profit and loss is intrinsic value at expiry. That is deliberate:
            the Optimizer tab's ordinary ranking holds implied volatility still to the horizon, and an earnings print
            is exactly the event that breaks that assumption.
          </dd>
          <dt>Return on risk · Chance</dt>
          <dd>
            The same two numbers as the Optimizer's cards. Return on risk is the profit at the worst point of the
            target over what the account puts up. Chance is the share of the option market's own implied distribution
            under which the position is profitable — a model number from a lognormal with no drift, not a forecast.
          </dd>
          <dt>"No structure pays at every point…"</dt>
          <dd>
            The honest answer rather than an empty list: it says how many shapes were enumerated and why each was
            dropped. "Lose at the target" means they made money somewhere in the range but not at both ends.
          </dd>
        </dl>

        <h3>When a row will not open</h3>
        <dl>
          <dt>No tradable, two-sided strikes near the money</dt>
          <dd>
            The chain exists but almost none of it passes the tradability check described under Wings. Common on the
            day of a report, and a real answer: those quotes are not ones you would want a structure built on.
          </dd>
          <dt>Missing calendar or missing history</dt>
          <dd>
            Without the FMP key the list is empty and says so; an empty list then means "not known", never "nobody
            reports today". A symbol with no past reports on file keeps its Implied column and loses its Past and
            Ratio.
          </dd>
        </dl>
      </div>
    </Modal>
  );
}
