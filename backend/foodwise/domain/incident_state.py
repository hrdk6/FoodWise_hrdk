"""Surplus incident state machine. Every status change goes through `transition`.

    detected -> open       safe to give away: find a nearby place
    detected -> rejected   below the large-surplus threshold; logged for learning
    open     -> arranged   a place agreed to take some of it
    arranged -> arranged   more places agreed
    arranged -> open       an arrangement fell through and nothing else is agreed
    open | arranged -> closed      handed over (impact recorded) or the rest disposed

When the safe window passes with nothing agreed, the surplus stays open but its route switches
from people to animal feed and compost (see services.incidents.expire).
"""

from __future__ import annotations

from foodwise.core.errors import Conflict

TRANSITIONS: dict[str, set[str]] = {
    "detected": {"rejected", "open"},
    "open": {"arranged", "closed", "cancelled"},
    "arranged": {"arranged", "open", "closed", "cancelled"},
    "rejected": set(),
    "closed": set(),
    "cancelled": set(),
}

ACTIVE = {"detected", "open", "arranged"}
TERMINAL = {"rejected", "closed", "cancelled"}


def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, set())


def transition(incident, new: str) -> None:
    if not can_transition(incident.status, new):
        raise Conflict(f"This surplus is {incident.status.replace('_', ' ')} and cannot move to {new.replace('_', ' ')}.")
    incident.status = new
