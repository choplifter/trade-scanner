import { Modal } from "../common/Modal";
import { BookRefLine, NATENBERG } from "./bookRefs";

interface ScreenerHelpProps {
  open: boolean;
  onClose: () => void;
}

/** How to read a screen, in the order the numbers should be read rather
 * than the order they sit in the table. Written against what the screener
 * actually does (app/options/screener.py) and against the traps that have
 * caught real runs here -- an expectancy computed off a mid nobody would
 * fill at, an open interest the feed never reported, a credit that looks
 * fat because the report inside the expiry has not happened yet. Keep this
 * and the criteria in step when one changes. */
export function ScreenerHelp({ open, onClose }: ScreenerHelpProps) {
  return (
    <Modal open={open} title="Finding something tradable" onClose={onClose} className="modal-panel-wide">
      <div className="options-help">
        <p className="options-help-intro">
          Most runs produce nothing worth placing, and that is the screen working rather than failing. If implied
          volatility is priced fairly, the expectancy of a premium structure is about zero before costs and below it
          after. A run that offers nothing is the normal state; a run that offers something is the exception you were
          looking for. Descriptive, not advice: every number here is a quote or a model value, and the decision is
          yours.
        </p>

        <h3>Read the columns in this order</h3>
        <dl>
          <dt>1 · Quote width — can it be filled at all?</dt>
          <dd>
            <strong>Where it is:</strong> the <strong>Worst leg</strong> column holds the widest of them all, which
            is the one that decides whether the package fills — sort by it ascending and what can actually be traded
            comes to the top. The individual numbers are the pair of percentages in the <strong>Put side</strong> and{" "}
            <strong>Call side</strong> columns. <code>295/305 · 10w · Δ0.16 · 23/92 % · 19 %</code> reads as: buy 295
            against the short 305, ten wide, the short at 0.16 delta, crossing the short's quote costs 23 % of its mid
            and crossing the wing's costs 92 %, and 19 % of the width comes back as credit. Either percentage turns red
            past the <strong>Quote ≤</strong> limit you set above the table.
            <br />
            This decides whether the rest of the row means anything. Credit, expectancy and risk-reward are computed
            from <em>mid</em> prices, so on a leg quoted 0.21 / 1.31 the mid is a number no one is offering. A row that
            fails it is not a worse opportunity than one that passes — it is an opportunity whose numbers are fiction.
            Read nothing else until this passes. Watch the second number especially: the wing sits furthest out of the
            money and is routinely the wider of the two, and it is the leg that stops a package filling at the mid.
          </dd>
          <dt>2 · IV / RV — is the premium actually expensive?</dt>
          <dd>
            Implied volatility against what the underlying has really done over the last twenty sessions. This is the
            only place a durable edge in selling premium can come from: the variance risk premium, implied sitting
            above realised. Above 1.20 counts as rich here. Below 1.00 you are selling something cheap, which is the
            wrong side of the trade whatever the credit looks like.
            <BookRefLine refs={[NATENBERG.historicalVol, NATENBERG.ivAsPredictor]} />
          </dd>
          <dt>2b · IV rank — expensive against its own year?</dt>
          <dd>
            Where today&apos;s implied volatility sits between its lowest and highest of the past 52 weeks. A different
            question from IV / RV: a quiet stock can be rich on IV / RV and still cheap against its own year. From 60 %
            it passes a premium-selling screen (green), up to 30 % a premium-buying one (red). A symbol with fewer than
            20 recorded sessions has no rank yet and is not judged on it — left out, not failed.
            <BookRefLine refs={[NATENBERG.ivAsPredictor]} />
          </dd>
          <dt>3 · EV — the one number that combines the others</dt>
          <dd>
            Every outcome weighted by its probability, in dollars per contract, integrated over the option market's
            own implied distribution. A structure can pay a large credit, lose rarely, and still be negative here —
            which is exactly what a list sorted by credit or by win rate hides. Carries the same caveat as everything
            else: it is built from mids, so a positive EV on a wide quote is not money.
            <BookRefLine refs={[NATENBERG.impliedDistributions, NATENBERG.goodSpread]} />
          </dd>
          <dt>4 · R/R and Loss prob — the shape, not the verdict</dt>
          <dd>
            Risk against reward, and the share of the distribution — the one the chain's strikes price, skew included — under which the structure is not ahead once
            every leg is filled at the natural instead of the mid — so a structure that ends a few cents up but
            not by what crossing the quotes cost counts as a loss here, as in the Optimizer and the ticket. Read
            them together or not at all: 29 : 1 at a 4 % chance of loss is a worse proposition than 3 : 1 at 25 %. Neither
            says anything on its own, which is why EV exists.
            <BookRefLine refs={[NATENBERG.probability, NATENBERG.impliedDistributions]} />
          </dd>
          <dt>5 · Earnings</dt>
          <dd>
            A report inside the expiry is why the premium is fat, and it collapses with the report. Early policy lets
            one through in the first third of the position's life — the volatility drops while the strikes are still
            far away and weeks of decay follow. Avoid fails any report inside. If a row is rich on IV/RV and holds a
            report, you are being paid for the event, not for the premium.
            <BookRefLine refs={[NATENBERG.gaps]} />
          </dd>
        </dl>

        <h3>When nothing passes, these are the levers</h3>
        <dl>
          <dt>Wing</dt>
          <dd>
            The width of the bought wing, in points, and the whole risk of the vertical. A one-point wing on a
            78-dollar ETF is 22.50 of maximum profit against 78 of risk, where eight legs of commission decide the
            sign; widening it raises both the credit and the risk and usually moves the expectancy. Blank aims at 3 %
            of spot. Worth trying two or three widths on the same symbol — the same chain can be a trade at one and
            not at another.
          </dd>
          <dt>Δ (short delta)</dt>
          <dd>
            How far out the short strikes sit. Nearer the money brings more credit and a higher chance of being
            touched; further out is the opposite, and past a point the strikes stop being quoted at all, which shows
            up as the quote-width criterion failing rather than as a worse price.
            <BookRefLine refs={[NATENBERG.delta]} />
          </dd>
          <dt>DTE</dt>
          <dd>
            The window the expiry is picked from. The screen prefers the monthly expiry inside it — the primary
            series, where open interest accumulates and quotes are tightest. A window with no monthly in it falls back
            to whatever is listed, which is usually a weekly and usually thinner.
            <BookRefLine refs={[NATENBERG.scalingForTime]} />
          </dd>
          <dt>Universe / Watchlist</dt>
          <dd>
            Watchlist answers "is this name I follow worth it today". Universe answers "is there anything worth it at
            all", over the most liquid few hundred optionable names. For the question you are usually asking, Universe
            is the honest one; Watchlist is for when you already have a name in mind.
          </dd>
        </dl>

        <h3>What the dashes mean</h3>
        <dl>
          <dt>OI reads "—"</dt>
          <dd>
            Open interest was not reported for that expiry at all. It is not zero: the data feed leaves the field
            unset for stretches of the session. The criterion then says nothing instead of failing every row, and the
            column you would normally use to tell a real wall from three contracts is simply unavailable. Contract
            volume beside it still works and is the fallback.
          </dd>
          <dt>EV, R/R or the spreads read "—"</dt>
          <dd>
            No structure could be built. Usually the wing: no listed strike far enough out, quoted on both sides, for
            the width being asked. Widening the wing or moving the delta band sometimes finds one; often the chain
            genuinely has nothing there.
          </dd>
        </dl>

        <h3>Before placing anything</h3>
        <p>
          Take the row to the Chain tab and price it as a real ticket. The preview uses live bid and ask, the
          account's level and limits, and warns where a market is too wide for a mid limit to fill — the screen's
          credit is an estimate, the preview's is what you would send. If the limit you would actually get is well
          inside the screen's credit, the expectancy that made the row interesting was never there.
        </p>
      </div>
    </Modal>
  );
}
