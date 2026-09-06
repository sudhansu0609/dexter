"""
Agent-loop unit tests (roadmap 7.1): mocked LLM responses driving Tier 2.

Covers the iteration cap, text-protocol JSON recovery, the invalid-argument
repair retry, the confirmation flow (per-session + TTL), cancellation, and the
fast-path fall-through regression for bug 4. No LLM, no network - the router's
`llm_router.chat` and `execute` are monkeypatched.

Run:  pytest backend/test_agent_loop.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cancellation
import router
import tools_core  # noqa: F401  - registers the real tool specs (confirm flags)
import tools_studio  # noqa: F401  - registers the Studio tools
from hybrid_llm import LLMResponse
from tool_registry import ToolResult


class ScriptedLLM:
    """Returns the queued responses in order; repeats the last one when empty."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.seen_messages = []

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.seen_messages.append(list(messages))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def resolve_provider(self, provider="auto"):
        # The tiered fast-model path pins both passes to one resolved backend.
        # Stubbed so these tests exercise the same route() branch the app takes
        # when DEXTER_FAST_MODEL is configured, instead of silently skipping it.
        return provider if provider and provider != "auto" else "ollama"


def answer(text):
    return LLMResponse(content=text, provider="fake", model="fake")


def tool_call(name, arguments):
    return LLMResponse(
        content="", tool_calls=[{"name": name, "arguments": arguments}],
        provider="fake", model="fake",
    )


@pytest.fixture()
def executed(monkeypatch):
    """Route tool execution into a recorder instead of the real tools."""
    calls = []

    def fake_execute(name, args):
        calls.append((name, args))
        return ToolResult(ok=True, summary=f"{name} done.")

    monkeypatch.setattr(router, "execute", fake_execute)
    return calls


def use_llm(monkeypatch, *responses):
    llm = ScriptedLLM(responses)
    monkeypatch.setattr(router, "llm_router", llm)
    return llm


@pytest.fixture()
def no_tiering(monkeypatch):
    """Route straight to Tier 2, bypassing the fast-model tier.

    These tests assert Tier-2 behaviour. Whether the fast tier is configured is
    a machine-local .env choice (DEXTER_FAST_MODEL), so pin it off here rather
    than letting the suite's meaning depend on the developer's environment.
    The fast tier has its own tests below.
    """
    monkeypatch.setattr(router, "FAST_MODEL", "")


# ───────────────────────── plain answers ─────────────────────────


def test_direct_answer_is_chat(monkeypatch, executed):
    use_llm(monkeypatch, answer("The capital of France is Paris."))
    result = router.agent_loop("what is the capital of france", session="t-a1")
    assert result.action == "chat"
    assert "Paris" in result.reply
    assert result.tool_calls == []
    assert executed == []


def test_json_answer_protocol_is_unwrapped(monkeypatch, executed):
    use_llm(monkeypatch, answer('{"answer": "All quiet on the desktop."}'))
    result = router.agent_loop("anything happening?", session="t-a2")
    assert result.reply == "All quiet on the desktop."
    assert result.action == "chat"


# ───────────────────────── tool calling ─────────────────────────


def test_native_tool_call_then_answer(monkeypatch, executed):
    use_llm(
        monkeypatch,
        tool_call("create_note", {"content": "buy milk"}),
        answer("Noted."),
    )
    result = router.agent_loop("note that I need to buy milk", session="t-b1")
    assert result.action == "agent"
    assert executed == [("create_note", {"content": "buy milk"})]
    assert result.reply == "Noted."


def test_text_protocol_tool_call_is_recovered(monkeypatch, executed):
    # Small local models often emit the JSON protocol as plain text instead of
    # using native tool calling; the loop must treat it identically.
    use_llm(
        monkeypatch,
        answer('{"tool": "create_note", "args": {"content": "call mom"}}'),
        answer("Saved the note."),
    )
    result = router.agent_loop("write this down: call mom", session="t-b2")
    assert executed == [("create_note", {"content": "call mom"})]
    assert result.reply == "Saved the note."


def test_iteration_cap_reports_partial_progress(monkeypatch, executed):
    # A model that never stops calling tools must be cut off at MAX_ITERATIONS
    # with an honest report, not looped forever.
    use_llm(monkeypatch, tool_call("get_agenda", {}))
    result = router.agent_loop("plan my day", session="t-b3")
    assert result.action == "agent_max_steps"
    assert len(executed) == router.MAX_ITERATIONS
    assert str(router.MAX_ITERATIONS) in result.reply


def test_invalid_arguments_get_one_repair_retry(monkeypatch):
    attempts = []

    def fake_execute(name, args):
        attempts.append((name, args))
        if len(attempts) == 1:
            return ToolResult(ok=False, summary="Invalid arguments: 'content' is required.")
        return ToolResult(ok=True, summary="Note saved.")

    monkeypatch.setattr(router, "execute", fake_execute)
    llm = use_llm(
        monkeypatch,
        tool_call("create_note", {}),
        tool_call("create_note", {"content": "fixed"}),
        answer("Done."),
    )

    result = router.agent_loop("take a note", session="t-b4")
    assert attempts == [("create_note", {}), ("create_note", {"content": "fixed"})]
    # The repair prompt must have been injected between the two calls.
    repair_turns = [
        m for m in llm.seen_messages[-1]
        if m["role"] == "user" and "invalid" in m["content"].lower()
    ]
    assert repair_turns, "expected a repair instruction in the conversation"
    assert result.reply == "Done."


def test_llm_offline_reported_plainly(monkeypatch, executed):
    use_llm(monkeypatch, LLMResponse(error="connection refused", provider="fake"))
    result = router.agent_loop("hello?", session="t-b5")
    assert result.action == "llm_offline"
    assert executed == []


# ───────────────────────── cancellation (barge-in) ─────────────────────────


def test_cancel_before_loop_interrupts(monkeypatch, executed):
    session = "t-c1"
    use_llm(monkeypatch, answer("should never be reached"))
    cancellation.request_cancel(session)
    result = router.agent_loop("long story please", session=session)
    assert result.action == "interrupted"
    assert result.reply == ""


def test_cancelled_response_interrupts(monkeypatch, executed):
    use_llm(monkeypatch, LLMResponse(cancelled=True, provider="fake"))
    result = router.agent_loop("tell me everything", session="t-c2")
    assert result.action == "interrupted"


# ───────────────────────── confirmation flow ─────────────────────────


def test_confirm_tool_parks_and_yes_executes(monkeypatch, executed):
    session = "t-d1"
    use_llm(monkeypatch, tool_call("write_file", {"path": "x.txt", "content": "hi"}))

    result = router.agent_loop("save hi to x.txt", session=session)
    assert result.needs_confirmation
    assert result.action == "confirm"
    assert executed == []  # parked, not run

    confirmed = router.route("yes", session=session)
    assert confirmed.action == "confirmed"
    assert executed == [("write_file", {"path": "x.txt", "content": "hi"})]


def test_confirmation_is_session_scoped(monkeypatch, executed):
    # A "yes" from another session must not fire session A's destructive call.
    use_llm(monkeypatch, tool_call("write_file", {"path": "x.txt", "content": "hi"}))
    router.agent_loop("save hi to x.txt", session="t-e1")

    use_llm(monkeypatch, answer("Nothing pending here."))
    other = router.route("yes", session="t-e2")
    assert executed == []
    assert other.action != "confirmed"

    router._take_pending("t-e1")  # cleanup


def test_no_answers_cancel_pending(monkeypatch, executed):
    session = "t-f1"
    use_llm(monkeypatch, tool_call("write_file", {"path": "x.txt", "content": "hi"}))
    router.agent_loop("save hi to x.txt", session=session)

    result = router.route("no, don't", session=session)
    assert result.action == "cancelled"
    assert executed == []
    assert not router._has_pending(session)


def test_pending_confirmation_expires(monkeypatch, executed):
    session = "t-g1"
    use_llm(monkeypatch, tool_call("write_file", {"path": "x.txt", "content": "hi"}))
    router.agent_loop("save hi to x.txt", session=session)

    # Age the entry past the TTL instead of sleeping through it.
    router._pending[session]["ts"] -= router._PENDING_TTL_SECONDS + 1
    assert not router._has_pending(session)

    use_llm(monkeypatch, answer("Nothing to confirm."))
    late = router.route("yes", session=session)
    assert executed == []
    assert late.action != "confirmed"


# ───────────────────────── fast-path fall-through (bug 4) ─────────────────────────


def test_fast_rule_with_unusable_args_falls_through(monkeypatch, executed, no_tiering):
    # "remind me ..." matches the reminder rule, but without a parseable time
    # the arg builder returns None. Bug 4 made fast_path return None for the
    # whole tier immediately; the fix keeps trying later rules/tiers.
    assert router.fast_path("remind me to breathe") is None

    # And the full route must end up in Tier 2 (the mocked agent), not error out.
    use_llm(monkeypatch, answer("When would you like the reminder?"))
    result = router.route("remind me to breathe", session="t-h1")
    assert result.tier == 2
    assert "reminder" in result.reply.lower() or result.reply


def test_fast_rule_still_matches_later_rule_after_fallthrough(monkeypatch, executed):
    # A matched-but-unbuildable rule must not shadow a later rule that fits:
    # "open github.com" hits launch_app's rule but is redirected to open_website.
    hit = router.fast_path("open github.com")
    assert hit is not None
    tool_name, args = hit
    assert tool_name == "open_website"
    assert args["url"] == "github.com"
