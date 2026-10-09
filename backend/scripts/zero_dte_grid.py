"""Full parameter grid for the 0DTE put credit spread backtest, offline from
the cached bars (backend/.cache/zero_dte, filled by the zero-dte-backtest
endpoint). In-sample 2024-2025, out-of-sample 2026.

Usage, from backend/: .venv/Scripts/python scripts/zero_dte_grid.py out.csv"""

import csv
import itertools
import math
import pickle
import statistics
import sys
from pathlib import Path
from datetime import date, time as clock
from multiprocessing import Pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.options.zero_dte import CACHE_DIR, Params, simulate_day  # noqa: E402
from app.services.market_clock import sessions_between  # noqa: E402

DELTAS = [0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30]
WIDTHS = [1.0, 2.0, 3.0, 5.0]
ENTRIES = ["09:45", "10:00", "10:30", "11:00", "12:00", "13:00", "14:00", "15:00"]
STOPS = [None, 2.0, 3.0, 5.0]
TARGETS = [None, 0.5, 0.8]

DATA = None
CLOSES = None


def init():
    global DATA, CLOSES
    files = sorted((CACHE_DIR / "SPY").glob("*.pkl"))
    DATA = [(date.fromisoformat(f.stem), pickle.loads(f.read_bytes())) for f in files]
    sess = sessions_between(DATA[0][0], DATA[-1][0])
    CLOSES = {d: sess[d][1].hour * 60 + sess[d][1].minute for d in sess}


def stats(pnls):
    n = len(pnls)
    if n < 2:
        return {"n": n, "mean": None, "t": None, "total": round(sum(pnls), 2)}
    m = statistics.mean(pnls)
    s = statistics.stdev(pnls)
    return {"n": n, "mean": round(m, 3), "t": round(m / (s / math.sqrt(n)), 2) if s > 0 else None, "total": round(sum(pnls), 2)}


def one(combo):
    delta, width, entry, stop, target = combo
    hh, mm = (int(x) for x in entry.split(":"))
    p = Params(entry=clock(hh, mm), short_delta=delta, width=width, stop_mult=stop, take_profit=target)
    trades, off = [], 0
    for d, bars in DATA:
        if not bars.puts or d not in CLOSES:
            continue
        t = simulate_day(d, bars, p, CLOSES[d])
        if t is None:
            continue
        if abs(abs(t.short_delta) - delta) > 0.05:
            off += 1
            continue
        trades.append(t)
    pnls = [t.pnl for t in trades]
    ins = [t.pnl for t in trades if t.day < "2026"]
    oos = [t.pnl for t in trades if t.day >= "2026"]
    eq = peak = dd = 0.0
    for x in pnls:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    a, i, o = stats(pnls), stats(ins), stats(oos)
    years = {y: round(sum(t.pnl for t in trades if t.day.startswith(y)), 2) for y in ("2024", "2025", "2026")}
    return {
        "delta": delta, "width": width, "entry": entry, "stop": stop or "", "target": target or "",
        "n": a["n"], "total": a["total"], "mean": a["mean"], "t": a["t"],
        "win": round(sum(x > 0 for x in pnls) / len(pnls), 3) if pnls else None,
        "worst": round(min(pnls), 2) if pnls else None, "max_dd": round(dd, 2),
        "y2024": years["2024"], "y2025": years["2025"], "y2026": years["2026"],
        "is_n": i["n"], "is_mean": i["mean"], "is_t": i["t"],
        "oos_n": o["n"], "oos_mean": o["mean"], "oos_t": o["t"], "oos_total": o["total"],
        "pinned": sum(t.pinned for t in trades), "off_delta": off,
        "avg_credit": round(statistics.mean(t.credit for t in trades) * 100, 2) if trades else None,
    }


if __name__ == "__main__":
    out_path = sys.argv[1]
    combos = list(itertools.product(DELTAS, WIDTHS, ENTRIES, STOPS, TARGETS))
    with Pool(initializer=init) as pool:
        rows = pool.map(one, combos, chunksize=8)
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} combinations -> {out_path}")
