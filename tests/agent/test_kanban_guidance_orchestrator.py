"""The effective prompt must carry ONE lifecycle for the card it describes.

``KANBAN_GUIDANCE`` is injected into every kanban-capable agent, and its
"Orchestrator mode" paragraph tells a decomposing agent to complete its own
card once it has fanned out. For an ordinary worker spawning a follow-up that
is right. For the profile named by ``kanban.orchestrator_profile`` it inverts
the lifecycle: that agent's card is the ROOT, which exists to wait for those
children and judge them, so completing it at fan-out time is exactly the
failure the orchestrator exists to prevent.

These tests pin the composition, not a SOUL's argument with it: a profile
document can restate the contract, but it cannot stop a contradictory sentence
from being in the same prompt.
"""

from __future__ import annotations

import pytest

from agent import prompt_builder as pb


# Imported, never retyped: the constant carries a trailing space, and a copy
# that drops it would let a reflowed sentence turn `.replace()` into a silent
# no-op while this file still reported the drift guard as green.
FANOUT_COMPLETE = pb._ORCHESTRATOR_FANOUT_COMPLETE


@pytest.fixture()
def board(monkeypatch):
    """Point the board's orchestrator at ``foreman`` and let the caller choose
    which profile is asking."""
    import hermes_cli.config as cfgmod
    import hermes_cli.profiles as profmod

    monkeypatch.setattr(
        cfgmod, "load_config", lambda *a, **k: {"kanban": {"orchestrator_profile": "foreman"}},
    )

    def _as(profile):
        monkeypatch.setattr(profmod, "get_active_profile_name", lambda: profile)

    return _as


def test_the_contradictory_sentence_is_really_in_the_shipped_guidance():
    """Drift guard. Every test below is a no-op if upstream rewords this, so
    fail loudly here rather than silently stop removing anything."""
    assert KANBAN_SENTENCE_COUNT() == 1


def KANBAN_SENTENCE_COUNT():
    return pb.KANBAN_GUIDANCE.count(FANOUT_COMPLETE)


def test_a_worker_still_gets_the_fanout_advice(board):
    """Negative control: for a worker spawning a follow-up the advice is
    correct and must survive untouched."""
    board("default")
    guidance = pb.kanban_guidance_for()
    assert FANOUT_COMPLETE in guidance
    assert guidance == pb.KANBAN_GUIDANCE


def test_the_orchestrator_never_sees_complete_your_own_card(board):
    """The fix: the sentence is gone from the effective prompt, replaced by the
    root contract, and nothing else in the block changed."""
    board("foreman")
    guidance = pb.kanban_guidance_for()
    assert FANOUT_COMPLETE not in guidance
    assert "do NOT complete your own task at fan-out time" in guidance
    # Everything else the block teaches is still there.
    for kept in (
        "# Kanban task execution protocol",
        "Do NOT execute the work yourself; your job is routing, not implementation.",
        "**Decision ownership.**",
        "Do not complete a task you didn't actually finish. Block it.",
    ):
        assert kept in guidance


def test_an_explicit_profile_argument_wins(board):
    """The resolution is per-profile, not per-process."""
    board("default")
    assert FANOUT_COMPLETE not in pb.kanban_guidance_for("foreman")
    assert FANOUT_COMPLETE in pb.kanban_guidance_for("default")


def test_without_a_configured_orchestrator_nothing_is_rewritten(monkeypatch):
    """Negative control: a board with no orchestrator has no root lifecycle to
    protect, so every profile gets the shipped text."""
    import hermes_cli.config as cfgmod
    import hermes_cli.profiles as profmod

    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: {"kanban": {}})
    monkeypatch.setattr(profmod, "get_active_profile_name", lambda: "foreman")
    assert pb.kanban_guidance_for() == pb.KANBAN_GUIDANCE
    assert pb.is_orchestrator_profile() is False


def test_agent_init_survives_a_prompt_builder_from_before_this_change(monkeypatch):
    """A gateway or desktop process that imported ``agent.prompt_builder``
    BEFORE an update keeps that module object in ``sys.modules`` while loading a
    newer ``agent_init`` after it. A hard ``from ... import kanban_guidance_for``
    against that stale module aborts agent init outright -- observed live as
    "agent init failed: cannot import name 'kanban_guidance_for'". A per-profile
    refinement of the prompt must degrade, never fail startup.
    """
    import model_tools
    from agent import agent_init

    monkeypatch.delattr(pb, "kanban_guidance_for", raising=True)
    monkeypatch.setattr(
        model_tools, "get_tool_definitions",
        lambda **kwargs: [{"function": {"name": "kanban_show"}}],
    )

    class _Agent:
        quiet_mode = True
        tools = None
        valid_tool_names = set()

    agent = _Agent()
    agent_init._load_tools(agent, None, None)
    assert agent._kanban_worker_guidance == pb.KANBAN_GUIDANCE


def test_a_guidance_helper_that_raises_does_not_fail_startup(monkeypatch):
    """Same contract for a helper that exists but blows up."""
    import model_tools
    from agent import agent_init

    def _boom(*args, **kwargs):
        raise RuntimeError("profile resolution exploded")

    monkeypatch.setattr(pb, "kanban_guidance_for", _boom)
    monkeypatch.setattr(
        model_tools, "get_tool_definitions",
        lambda **kwargs: [{"function": {"name": "kanban_show"}}],
    )

    class _Agent:
        quiet_mode = True
        tools = None
        valid_tool_names = set()

    agent = _Agent()
    agent_init._load_tools(agent, None, None)
    assert agent._kanban_worker_guidance == pb.KANBAN_GUIDANCE


def test_an_unreadable_config_does_not_break_prompt_building(monkeypatch):
    """Prompt composition must never be what takes a session down."""
    import hermes_cli.config as cfgmod

    def _boom(*a, **k):
        raise RuntimeError("config.yaml is unreadable")

    monkeypatch.setattr(cfgmod, "load_config", _boom)
    assert pb.kanban_guidance_for() == pb.KANBAN_GUIDANCE
