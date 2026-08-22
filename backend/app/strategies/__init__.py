"""Strategy scripts: drop a .py file in here and it trades and backtests.

Deliberately a sibling of app.indicators rather than living inside it. The
indicator loader re-executes its files per /bars request and nothing outside
the chart endpoint can reach them, so an indicator cannot inform a signal.
A strategy has to be callable from the scanner, from the backtest walk and
from the order ticket, which is why the package sits here at the top level.

See loader.py for the file contract and context.py for what a strategy is
allowed to look at.
"""
