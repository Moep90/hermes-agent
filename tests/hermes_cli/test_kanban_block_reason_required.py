"""Every blocked card must carry one authoritative current reason.

A card can reach ``blocked`` three ways, and before this each one was allowed
to record nothing:

* ``kanban_block`` (worker tool) -- already required a reason;
* ``hermes kanban block <id>`` with no words -- recorded ``reason: None``;
* a dashboard drag to the Blocked column -- recorded ``reason: None``.

A NULL reason is indistinguishable from a bug: the operator cannot answer it,
the orchestrator cannot reconcile it, and ``_rule_stuck_in_blocked`` tells the
reader to "check the block reason" that does not exist. The breaker path is the
fourth way and was always attributable (``last_failure_error`` plus the
``gave_up`` payload); these tests pin that too, so a refactor cannot quietly
drop it.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest


def _owned_modules():
    return [
        n for n in list(sys.modules)
        if n.startswith(("hermes_cli", "hermes_state")) or n == "hermes_constants"
    ]


@pytest.fixture()
def board(monkeypatch):
    home = tempfile.mkdtemp(prefix="kanban_block_reason_test_")
    monkeypatch.setenv("HERMES_HOME", home)
    # Evict to re-import against this HERMES_HOME, and restore afterwards: a
    # purge left in place gives later files in the same process a second copy
    # of hermes_cli.* that their captured bindings never point at.
    evicted = {name: sys.modules[name] for name in _owned_modules()}
    for name in evicted:
        del sys.modules[name]
    try:
        from hermes_cli import kanban_db
        kanban_db.init_db()
        yield kanban_db
    finally:
        for name in _owned_modules():
            del sys.modules[name]
        sys.modules.update(evicted)


def _blocked_reason(kb, conn, task_id):
    """The reason a reader can actually retrieve for the current blocked state."""
    for event in reversed(kb.list_events(conn, task_id)):
        if event.kind in ("blocked", "block_loop_detected", "dependency_wait"):
            return (event.payload or {}).get("reason")
        if event.kind == "gave_up":
            return (event.payload or {}).get("error")
    return None


def test_a_blank_reason_is_refused(board):
    """The common layer refuses, so no caller can create a reasonless block."""
    kb = board
    from hermes_cli import kanban_db_connect as kbc
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="needs a reason", assignee="default")
        for blank in (None, "", "   "):
            with pytest.raises(ValueError, match="reason is required"):
                kb.block_task(conn, tid, reason=blank)
        # The refusal is total: no half-transition left behind.
        assert kb.get_task(conn, tid).status == "ready"
        assert [e.kind for e in kb.list_events(conn, tid)] == ["created"]


def test_a_worker_block_keeps_working(board):
    """Negative control: the path that always supplied a reason is unchanged."""
    kb = board
    from hermes_cli import kanban_db_connect as kbc
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="needs a credential", assignee="default")
        kb.claim_task(conn, tid)
        assert kb.block_task(
            conn, tid, reason="the staging API token is missing", kind="needs_input",
            expected_run_id=kb.get_task(conn, tid).current_run_id,
        )
        assert kb.get_task(conn, tid).status == "blocked"
        assert _blocked_reason(kb, conn, tid) == "the staging API token is missing"


def test_the_cli_reports_the_missing_reason_instead_of_blocking(board):
    """``hermes kanban block <id>`` with no words is an operator error, not a
    traceback and not a silent reasonless block."""
    kb = board
    import argparse
    from hermes_cli import kanban as kanban_cli
    from hermes_cli import kanban_db_connect as kbc
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="blocked by hand", assignee="default")

    rc = kanban_cli._cmd_block(argparse.Namespace(task_id=tid, reason=[], kind=None, ids=None))
    assert rc != 0
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "ready"

    rc = kanban_cli._cmd_block(argparse.Namespace(
        task_id=tid, reason=["waiting", "on", "legal"], kind="needs_input", ids=None))
    assert rc == 0
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "blocked"
        assert _blocked_reason(kb, conn, tid) == "waiting on legal"


def test_a_dashboard_drag_records_its_provenance(board):
    """The dashboard sends no prose. It must still be attributable."""
    kb = board
    from hermes_cli import kanban_db_connect as kbc
    from plugins.kanban.dashboard.plugin_api import _STATUS_HANDLERS

    class _Payload:
        block_reason = None
        summary = None
        metadata = None
        result = None
        assignee = None

    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="dragged to blocked", assignee="default")
        assert _STATUS_HANDLERS["blocked"](conn, tid, _Payload())
        assert kb.get_task(conn, tid).status == "blocked"
        reason = _blocked_reason(kb, conn, tid)
        assert reason and "dashboard" in reason

        other = kb.create_task(conn, title="dragged with prose", assignee="default")
        payload = _Payload()
        payload.block_reason = "duplicate of t_0000"
        assert _STATUS_HANDLERS["blocked"](conn, other, payload)
        assert _blocked_reason(kb, conn, other) == "duplicate of t_0000"


def test_the_breaker_path_stays_attributable(board):
    """The fourth way in: no reason argument anywhere, but the error is durable
    on the row AND on the event, and survives reopening the board."""
    kb = board
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_dispatch as kbd
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="keeps crashing", assignee="default")
        for _ in range(2):
            kb.claim_task(conn, tid)
            kbd._record_task_failure(
                conn, tid, error="ImportError: no module named 'pyarrow'",
                outcome="crashed", failure_limit=2, release_claim=True, end_run=True,
            )
        assert kb.get_task(conn, tid).status == "blocked"
    with kbc.connect_closing() as conn:
        assert "pyarrow" in (kb.get_task(conn, tid).last_failure_error or "")
        assert "pyarrow" in (_blocked_reason(kb, conn, tid) or "")
