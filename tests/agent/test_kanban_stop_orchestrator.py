"""The turn-end guard must not ask the orchestrator for the wrong terminal call.

``build_kanban_stop_nudge`` fires when a kanban worker ends a turn without a
board tool. Its generic steps assume the agent is holding a deliverable:
"finish the file, then complete". For the profile that owns orchestration roots
both steps are wrong — it must not implement, and completing its card at
fan-out time destroys the root that was supposed to judge the children.

A dependency block is the correct parked state AND a terminal board call, so
the guard keeps its protocol-violation contract while asking for the right
thing. Same class of defect as the "Orchestrator mode" paragraph in
KANBAN_GUIDANCE, one layer down.
"""

from __future__ import annotations

import pytest

from agent import kanban_stop
from agent import prompt_builder as pb


WORKER_STEP = "Finish any remaining deliverable"
ROOT_STEP = "do NOT complete this card"


@pytest.fixture()
def as_worker(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_abc123")
    monkeypatch.delenv("HERMES_KANBAN_STOP_NUDGE", raising=False)

    def _role(is_orchestrator):
        monkeypatch.setattr(pb, "is_orchestrator_profile", lambda *a, **k: is_orchestrator)

    return _role


def test_a_worker_is_still_told_to_finish_its_deliverable(as_worker):
    """Negative control: the generic contract is unchanged for everyone else."""
    as_worker(False)
    nudge = kanban_stop.build_kanban_stop_nudge(messages=[], attempts=0)
    assert nudge is not None
    assert WORKER_STEP in nudge
    assert ROOT_STEP not in nudge
    assert "kanban_complete" in nudge


def test_the_orchestrator_is_told_to_park_the_root_not_complete_it(as_worker):
    as_worker(True)
    nudge = kanban_stop.build_kanban_stop_nudge(messages=[], attempts=0)
    assert nudge is not None
    assert WORKER_STEP not in nudge
    assert ROOT_STEP in nudge
    assert "kanban_block(kind='dependency', reason=...)" in nudge
    # It still demands a terminal board call, which is the guard's whole point.
    assert "protocol violation" in nudge


def test_the_guard_still_stands_down_when_it_should(as_worker):
    """Orchestrator or not, the nudge must not fire once the board was told."""
    as_worker(True)
    terminal = [{"role": "assistant", "tool_calls": [{"function": {"name": "kanban_block"}}]}]
    assert kanban_stop.build_kanban_stop_nudge(messages=terminal, attempts=0) is None
    assert kanban_stop.build_kanban_stop_nudge(messages=[], attempts=2, max_attempts=2) is None


def test_a_broken_profile_lookup_falls_back_to_the_worker_text(as_worker, monkeypatch):
    """The guard must never be what takes a turn down."""
    as_worker(False)

    def _boom(*a, **k):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(pb, "is_orchestrator_profile", _boom)
    nudge = kanban_stop.build_kanban_stop_nudge(messages=[], attempts=0)
    assert nudge is not None and WORKER_STEP in nudge
