"""Per-user broker credentials: each login connects its own Alpaca account
(paper and/or live) while market data stays on the operator's keys. See
crypto (secrets at rest), store (the table) and resolver (which
TradingClient a request gets)."""
