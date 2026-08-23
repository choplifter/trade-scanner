"""Discovering strategy scripts.

The loader is what makes a strategy a drop-in file rather than a registry
entry, so the things worth pinning are the ones that fail quietly: a file
that does not satisfy the contract, a file parked with ENABLED = False, and
a name that matches nothing.

The last one matters more here than for indicators. A missing indicator is a
missing line on a chart -- you see it. A strategy that failed to load looks
exactly like a strategy that found no setups, which is why load_strategies
returns its failures instead of only logging them.
"""

import pathlib
import textwrap

import pytest

from app.strategies import loader as loader_mod
from app.strategies import switches
from app.strategies.context import Signal, StrategyContext
from app.strategies.loader import available_names, inventory, load_strategies


@pytest.fixture
def strategy_dir(tmp_path, monkeypatch):
    """Point the loader at a scratch directory.

    The real directory is deliberately not used: these assert the loader's
    behaviour, and pinning them to whichever strategies happen to ship would
    make them fail every time one is added.
    """
    monkeypatch.setattr(loader_mod, "_DIR", tmp_path)
    return tmp_path


def _write(directory: pathlib.Path, filename: str, body: str) -> None:
    (directory / filename).write_text(textwrap.dedent(body), encoding="utf-8")


_GOOD = '''
    NAME = "Test Rule"

    def evaluate(ctx):
        return None
'''


# --- discovery -----------------------------------------------------------


def test_a_dropped_in_file_is_found_without_registration(strategy_dir):
    """The whole point: no import to add, no dict to edit."""
    _write(strategy_dir, "test_rule.py", _GOOD)

    strategies, errors = load_strategies()

    assert [s.name for s in strategies] == ["Test Rule"]
    assert errors == []


def test_the_loaders_own_files_are_never_treated_as_strategies(strategy_dir):
    _write(strategy_dir, "__init__.py", "")
    _write(strategy_dir, "context.py", "NAME = 'nope'")
    _write(strategy_dir, "loader.py", "NAME = 'nope'")
    _write(strategy_dir, "real.py", _GOOD)

    strategies, errors = load_strategies()

    assert [s.name for s in strategies] == ["Test Rule"]
    assert errors == []


def test_files_load_in_a_stable_order(strategy_dir):
    """Sorted, so a report lists rules the same way twice running."""
    _write(strategy_dir, "zulu.py", _GOOD.replace("Test Rule", "Zulu"))
    _write(strategy_dir, "alpha.py", _GOOD.replace("Test Rule", "Alpha"))

    strategies, _ = load_strategies()

    assert [s.name for s in strategies] == ["Alpha", "Zulu"]


def test_an_empty_directory_is_empty_not_an_error(strategy_dir):
    assert load_strategies() == ([], [])


# --- the contract --------------------------------------------------------


def test_a_file_without_evaluate_is_reported_not_silently_skipped(strategy_dir):
    """The failure this exists for: a strategy that never loads produces no
    signals, which is indistinguishable from a quiet market."""
    _write(strategy_dir, "broken.py", "NAME = 'Broken'\n")

    strategies, errors = load_strategies()

    assert strategies == []
    assert len(errors) == 1
    assert errors[0].filename == "broken.py"
    assert "evaluate" in errors[0].error


def test_a_file_without_a_name_is_reported(strategy_dir):
    _write(strategy_dir, "nameless.py", "def evaluate(ctx):\n    return None\n")

    strategies, errors = load_strategies()

    assert strategies == []
    assert "NAME" in errors[0].error


def test_a_blank_name_is_refused(strategy_dir):
    """An empty name would render as a blank row rather than as a fault."""
    _write(strategy_dir, "blank.py", "NAME = '   '\n\ndef evaluate(ctx):\n    return None\n")

    _, errors = load_strategies()

    assert "NAME" in errors[0].error


def test_a_file_that_raises_on_import_is_reported_with_its_type(strategy_dir):
    _write(strategy_dir, "boom.py", "raise RuntimeError('kaboom')\n")

    strategies, errors = load_strategies()

    assert strategies == []
    assert "RuntimeError" in errors[0].error
    assert "kaboom" in errors[0].error


def test_one_bad_file_does_not_cost_the_good_ones(strategy_dir):
    _write(strategy_dir, "broken.py", "raise RuntimeError('kaboom')\n")
    _write(strategy_dir, "fine.py", _GOOD)

    strategies, errors = load_strategies()

    assert [s.name for s in strategies] == ["Test Rule"]
    assert [e.filename for e in errors] == ["broken.py"]


# --- parking a strategy --------------------------------------------------


def test_enabled_false_parks_a_file_without_deleting_it(strategy_dir):
    _write(strategy_dir, "parked.py", "ENABLED = False\n" + textwrap.dedent(_GOOD))

    strategies, errors = load_strategies()

    assert strategies == []
    assert errors == []


def test_enabled_defaults_to_true(strategy_dir):
    _write(strategy_dir, "on.py", _GOOD)

    strategies, _ = load_strategies()

    assert len(strategies) == 1


# --- the runtime switch --------------------------------------------------


def test_a_switched_off_strategy_stops_loading(strategy_dir):
    _write(strategy_dir, "rule.py", _GOOD)
    switches.set_switched("rule", False)

    strategies, errors = load_strategies()

    assert strategies == []
    assert errors == []


def test_a_switch_defaults_to_on(strategy_dir):
    """A freshly dropped-in file must start live -- the drop-in-and-it-works
    behaviour the loader exists for."""
    _write(strategy_dir, "rule.py", _GOOD)

    assert len(load_strategies()[0]) == 1


def test_switching_back_on_restores_the_strategy(strategy_dir):
    _write(strategy_dir, "rule.py", _GOOD)
    switches.set_switched("rule", False)
    switches.set_switched("rule", True)

    assert [s.name for s in load_strategies()[0]] == ["Test Rule"]


def test_only_bypasses_the_switch(strategy_dir):
    """Asking for a rule by name is a stronger statement than a saved toggle
    -- it is how a parked rule still gets backtested before being turned
    back on."""
    _write(strategy_dir, "rule.py", _GOOD)
    switches.set_switched("rule", False)

    strategies, _ = load_strategies(only="rule")

    assert [s.name for s in strategies] == ["Test Rule"]


def test_a_corrupt_switch_file_means_everything_on(strategy_dir):
    """Failing toward on: a strategy silently off looks exactly like a quiet
    market."""
    _write(strategy_dir, "rule.py", _GOOD)
    switches._PATH.write_text("not json{", encoding="utf-8")

    assert len(load_strategies()[0]) == 1


def test_the_inventory_lists_switched_off_strategies(strategy_dir):
    """A panel that hides whatever is off can never turn it back on."""
    _write(strategy_dir, "on_rule.py", _GOOD.replace("Test Rule", "On"))
    _write(strategy_dir, "off_rule.py", _GOOD.replace("Test Rule", "Off"))
    switches.set_switched("off_rule", False)

    states, errors = inventory()

    assert errors == []
    assert [(s.name, s.stem, s.enabled) for s in states] == [
        ("Off", "off_rule", False),
        ("On", "on_rule", True),
    ]


def test_the_inventory_still_hides_parked_files(strategy_dir):
    """ENABLED = False is the file's own statement that it is out of service,
    not a runtime toggle -- the panel must not offer to flip it."""
    _write(strategy_dir, "parked.py", "ENABLED = False\n" + textwrap.dedent(_GOOD))

    states, errors = inventory()

    assert states == []
    assert errors == []


# --- selecting one ------------------------------------------------------


def test_a_single_strategy_can_be_selected_by_name(strategy_dir):
    _write(strategy_dir, "a.py", _GOOD.replace("Test Rule", "Alpha"))
    _write(strategy_dir, "b.py", _GOOD.replace("Test Rule", "Beta"))

    strategies, _ = load_strategies(only="Beta")

    assert [s.name for s in strategies] == ["Beta"]


def test_a_single_strategy_can_be_selected_by_filename(strategy_dir):
    """Typing the stem is what a CLI user will reach for first."""
    _write(strategy_dir, "vwap_reclaim.py", _GOOD.replace("Test Rule", "VWAP Reclaim"))

    strategies, _ = load_strategies(only="vwap_reclaim")

    assert [s.name for s in strategies] == ["VWAP Reclaim"]


def test_selecting_something_that_does_not_exist_yields_nothing(strategy_dir):
    """"No such strategy" and "no signals" must not look alike -- the caller
    reports the difference, so the loader has to make it visible."""
    _write(strategy_dir, "a.py", _GOOD)

    strategies, errors = load_strategies(only="nope")

    assert strategies == []
    assert errors == []


# --- what a loaded strategy does -----------------------------------------


def test_a_loaded_strategy_evaluates(strategy_dir):
    _write(
        strategy_dir,
        "buyer.py",
        '''
        NAME = "Always"

        from app.strategies.context import Signal

        def evaluate(ctx):
            return Signal(
                strategy=NAME,
                entry_price=ctx.bar.close,
                stop_price=ctx.bar.close * 0.98,
                target_price=ctx.bar.close * 1.04,
                reason="test",
            )
        ''',
    )

    strategy = load_strategies()[0][0]
    bar = type("_B", (), {"close": 10.0})()
    ctx = StrategyContext(
        symbol="AAA",
        bar=bar,
        session_bars=[bar],
        session_vwaps=[None],
        premarket_vwap=None,
    )

    signal = strategy.evaluate(ctx)

    assert isinstance(signal, Signal)
    assert signal.entry_price == 10.0
    assert signal.reward_ratio == pytest.approx(2.0)


def test_a_strategy_keeps_the_filename_for_error_messages(strategy_dir):
    """A report names the strategy; a fault names the file to go and fix."""
    _write(strategy_dir, "vwap_reclaim.py", _GOOD.replace("Test Rule", "VWAP Reclaim"))

    strategy = load_strategies()[0][0]

    assert (strategy.name, strategy.filename) == ("VWAP Reclaim", "vwap_reclaim.py")


def test_an_exception_inside_evaluate_is_not_swallowed(strategy_dir):
    """Deliberate. The backtest must stop rather than quietly report fewer
    signals than the rule actually produced; the live path wraps its own call
    so one bad file cannot stall the poll loop. Opposite needs, so the loader
    takes no view."""
    _write(
        strategy_dir,
        "thrower.py",
        "NAME = 'Thrower'\n\ndef evaluate(ctx):\n    raise ValueError('inside')\n",
    )

    strategy = load_strategies()[0][0]

    with pytest.raises(ValueError, match="inside"):
        strategy.evaluate(None)


# --- the real directory --------------------------------------------------


def test_every_shipped_strategy_satisfies_the_contract():
    """Runs the real loader over the real directory, the way
    test_indicator_timeframes does -- so a malformed file that ships shows up
    here rather than as an absent signal in production."""
    strategies, errors = load_strategies()

    assert errors == [], f"strategy files failed to load: {errors}"
    assert available_names() == [s.name for s in strategies]


def test_every_infrastructure_file_here_is_excluded():
    """The trap this closes: a helper module added to the strategy directory
    is loaded as a strategy, fails validation, and shows up as a load error
    on every run. Caught exactly that way when runner.py landed."""
    import pathlib

    import app.strategies as package

    on_disk = {p.name for p in pathlib.Path(package.__file__).parent.glob("*.py")}
    strategies, errors = load_strategies()

    assert errors == [], f"a non-strategy file is being loaded as one: {errors}"
    # Every file is either excluded infrastructure or a loaded strategy.
    accounted = loader_mod._EXCLUDED | {s.filename for s in strategies}
    assert on_disk <= accounted, f"unaccounted files: {on_disk - accounted}"


def test_strategy_names_are_unique():
    """Two rules under one name would pool their picks in a report and read
    as one strategy with twice the sample."""
    names = available_names()

    assert len(names) == len(set(names))
