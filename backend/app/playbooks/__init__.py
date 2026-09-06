"""Playbooks: the third script family, for options campaigns that live for
weeks and act on positions -- sell a put, get assigned, sell calls, get
called away, roll. Drop a .py file in here and it is offered as a playbook.

Deliberately a sibling of app.strategies rather than a strategy: a strategy
is stateless, evaluated per bar, on shares, and answers with a Signal
(entry, stop, target). A playbook is stateful (a campaign with a phase, a
cost basis and a history), event-driven (a fill, an expiry, an assignment)
and answers with an *action on option positions* -- SellPut, SellCall,
Roll, Close or Hold -- that the app proposes and the user places, or, in
the simulated account with the switch on, the runner places itself.

See loader.py for the file contract, context.py for what a playbook is
allowed to look at, actions.py for what it may answer, and runner.py for
how a campaign's proposal is refreshed and executed. wheel.py is the first
script.
"""
