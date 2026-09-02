"""The options-specific gate on top of app.trading.guards.assert_can_trade:
Alpaca approves options trading per account in levels, and a spread needs
level 3 on the account the order goes to (paper and live are approved
separately)."""

from app.trading.errors import OptionsNotApproved


def assert_options_level(level: int | None, needed: int, strategy: str) -> None:
    if level is None or level < needed:
        have = "no options approval" if not level else f"options level {level}"
        raise OptionsNotApproved(
            f"{strategy.replace('_', ' ')} needs options trading level {needed}; this account has {have}.",
            field="strategy",
        )
