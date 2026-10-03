"""Seed option_iv_history from Barchart's per-symbol "Options Overview
History" exports, so those symbols have an IV rank without a year of
recording first.

Download per symbol: barchart.com -> quote -> Options Overview History ->
Select History 1Y -> download. Then, from backend/:

    python -m scripts.import_barchart_iv [--dir PATH] [--as-of YYYY-MM-DD] [--dry-run]

Only fills days a symbol has no reading for -- our own readings stay, and
later ones replace a seeded day (IvHistoryStore.record). Seeded rows carry
source='barchart' and can be removed with
    DELETE FROM option_iv_history WHERE source = 'barchart'
See SOURCE_BARCHART in app.options.iv_history_store for why the two
measurements are close enough to share a history.
"""

import argparse
import os
from datetime import date

from app.core.config import get_settings
from app.options.barchart_iv import newest_session, parse_history, symbol_from_filename
from app.options.iv_history_store import SOURCE_BARCHART, IvHistoryStore

# Barchart's IV is a 30-day figure; recorded as 30 days so it sits inside
# the comparable band the rank reads.
BARCHART_DTE = 30

# Symbols whose Barchart IV is not the measurement ours is. LQD: Barchart
# read 13.9 % on 2026-10-02 where the chain's at-the-money prices give
# 9.7 % (Alpaca's call/put mean, an IV solved from the mids, and the
# straddle all agree), most likely because the put skew is steep and
# nearly all of LQD's open interest sits in hedging puts. Seeded, our
# reading against Barchart's year put the rank at 51 % for Barchart's 90 %.
EXCLUDED = {"LQD"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", default=os.path.join(os.path.expanduser("~"), "Downloads"))
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, help="last session in the files (default: newest row)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files: dict[str, str] = {}
    for name in sorted(os.listdir(args.dir)):
        symbol = symbol_from_filename(name)
        # A browser's "(1)" duplicate of the same export: one is enough.
        if symbol in EXCLUDED:
            print(f"{symbol:6s} skipped: excluded (see EXCLUDED)")
            continue
        if symbol and symbol not in files:
            files[symbol] = os.path.join(args.dir, name)
    if not files:
        print(f"No *_options-overview-history-*.csv in {args.dir}")
        return

    store = IvHistoryStore(get_settings().scanner_history_db_path)
    if not args.dry_run:
        store._init_schema_sync()
    total = 0
    for symbol, path in files.items():
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
        as_of = args.as_of or newest_session(text)
        readings = parse_history(text, as_of=as_of) if as_of else []
        added = 0 if args.dry_run else store.seed_sync(symbol, readings, source=SOURCE_BARCHART, dte=BARCHART_DTE)
        total += added
        span = f"{readings[0][0]} to {readings[-1][0]}" if readings else "nothing in the window"
        print(f"{symbol:6s} {len(readings):4d} readings ({span}), {added} added")
    print(f"{'(dry run) ' if args.dry_run else ''}{total} rows added across {len(files)} symbols")


if __name__ == "__main__":
    main()
