"""Turn-end guard for kanban workers, which must end with ``kanban_complete`` or
``kanban_block``. Some models narrate the next step and stop with no tool calls;
Hermes treats that as a clean exit → ``rc=0`` → dispatcher ``protocol_violation``.
Policy-only: return a bounded synthetic nudge so the loop continues instead of exiting.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from agent.delegation_context import owned_kanban_task


_TERMINAL_KANBAN_TOOLS = frozenset({"kanban_complete", "kanban_block"})

_DEFAULT_MAX_ATTEMPTS = 2


def kanban_stop_nudge_enabled() -> bool:
    """On when ``HERMES_KANBAN_TASK`` is set for the dispatcher-owned worker, unless
    ``HERMES_KANBAN_STOP_NUDGE`` disables it. In-process delegate_task children and cron runs
    inherit the env var but own no board task and carry no kanban toolset."""
    if (os.environ.get("HERMES_KANBAN_STOP_NUDGE") or "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return bool(owned_kanban_task())


def _tool_call_name(tc: Any) -> str:
    """Tool name from a dict or object tool call (``function.name`` first, then ``name``)."""
    if isinstance(tc, dict):
        fn = tc.get("function")
        return str((fn.get("name") if isinstance(fn, dict) else tc.get("name")) or "")
    fn = getattr(tc, "function", None)
    return str((getattr(fn, "name", "") if fn is not None else getattr(tc, "name", "")) or "")


def session_called_kanban_terminal(messages: Iterable[dict] | None) -> bool:
    """True if this conversation already invoked a terminal kanban tool."""
    for msg in filter(lambda m: isinstance(m, dict), messages or ()):
        role = msg.get("role")
        if role == "assistant" and any(
            _tool_call_name(tc) in _TERMINAL_KANBAN_TOOLS for tc in msg.get("tool_calls") or []
        ):
            return True
        if role == "tool" and str(msg.get("name") or "") in _TERMINAL_KANBAN_TOOLS:
            return True
    return False


def build_kanban_stop_nudge(
    *,
    messages: Iterable[dict] | None = None,
    attempts: int = 0,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    task_id: Optional[str] = None,
) -> Optional[str]:
    """Synthetic follow-up when a kanban worker exits without a terminal tool; ``None`` when
    the guard should not fire (not a kanban worker, already completed/blocked, budget exhausted)."""
    if (
        not kanban_stop_nudge_enabled()
        or attempts >= max_attempts
        or session_called_kanban_terminal(messages)
    ):
        return None

    tid = (task_id or os.environ.get("HERMES_KANBAN_TASK") or "").strip() or "this task"
    # The generic steps are written for a worker holding a deliverable. For the
    # profile that owns orchestration roots they invert the lifecycle: its card
    # is the root, "finish the deliverable" is work it must never do, and
    # completing at fan-out time destroys the only card that reconciles the
    # goal. A dependency block is a terminal board call AND the correct parked
    # state, so the guard keeps its contract without asking for the wrong one.
    try:
        from agent.prompt_builder import is_orchestrator_profile

        orchestrating = is_orchestrator_profile()
    except Exception:
        orchestrating = False
    if orchestrating:
        steps = (
            "1. If you have already fanned this goal out into child cards, do "
            "NOT complete this card — it is the root, and completing it at "
            "fan-out time is the judgment you have not made yet. Park it with "
            "`kanban_block(kind='dependency', reason=...)`: that IS a terminal "
            "board call, and the board re-promotes the root when its children "
            "finish.\n"
            "2. Otherwise call `kanban_complete(summary=...)` when every "
            "acceptance criterion is met with evidence, or "
            "`kanban_block(reason=...)` when it needs a human decision.\n\n"
        )
    else:
        steps = (
            "1. Finish any remaining deliverable (write the required file(s) now).\n"
            "2. Call `kanban_complete(summary=..., artifacts=[...])` if the work "
            "is done, OR `kanban_block(reason=...)` if you are blocked.\n\n"
        )
    return (
        "[System: You are a Hermes kanban worker. A plain-text reply is NOT a "
        "terminal state for the board.\n\n"
        f"Task `{tid}` is still `running`. Ending now without a board tool "
        "causes a protocol violation (clean exit with no "
        "`kanban_complete` / `kanban_block`).\n\n"
        "Do this immediately in your next response — do not narrate intent:\n"
        + steps +
        "Never end a turn with only a promise of future action. Repeated "
        "protocol violations will block this task and require manual intervention.]"
    )


__all__ = ["build_kanban_stop_nudge", "kanban_stop_nudge_enabled", "session_called_kanban_terminal"]
