"""
Ollama's native `/api/chat` (roadmap v13, P1).

Why this path exists at all, measured on this machine before it was written: a
thinking model asked through `/v1/chat/completions` spends its entire token
budget reasoning and hands back empty content, because the OpenAI schema has no
way to say "do not think". The same question through `/api/chat` with
`think:false` answers in 0.6 s. The native endpoint is also the only way to say
where a model should live (`options.num_gpu`) or how long it should stay
(`keep_alive`), which is what Phase 3 is built on.

Everything here runs against a fake `Session`. Nothing in this file may reach a
real Ollama: it is on the owner's machine with somebody's model in it.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hybrid_llm import ONLY_THOUGHT, HybridLLMRouter


class FakeResponse:
    """One canned HTTP answer, streamed line by line when asked."""

    def __init__(self, status=200, body=None, lines=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self._lines = list(lines or [])
        self.text = text or (json.dumps(self._body) if body is not None else "")
        self.encoding = None
        self.closed = False

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeOllama:
    """Records every POST and answers with whatever the test queued."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, json=None, headers=None, timeout=None, stream=False):
        self.posts.append({"url": url, "body": json, "stream": stream})
        if not self.responses:
            raise AssertionError(f"no canned response left for {url}")
        return self.responses.pop(0)

    @property
    def last(self):
        return self.posts[-1]["body"]


def _router(monkeypatch, *responses):
    router = HybridLLMRouter()
    router.ollama_url = "http://127.0.0.1:11434/v1"
    fake = FakeOllama(*responses)
    monkeypatch.setattr(router, "session", fake)
    return router, fake


def _chunk(content="", **extra):
    return json.dumps({"message": {"role": "assistant", "content": content}, "done": False, **extra})


MESSAGES = [{"role": "user", "content": "capital of italy?"}]


# ───────────────────────── content ─────────────────────────


def test_the_ollama_provider_goes_to_api_chat_not_the_openai_shim(monkeypatch):
    router, fake = _router(monkeypatch, FakeResponse(body={
        "message": {"role": "assistant", "content": "Rome."},
        "done": True, "eval_count": 12, "eval_duration": 60_000_000, "load_duration": 1_000_000,
    }))
    out = router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.3, 200, None, 30, None)

    assert out.ok and out.content == "Rome."
    assert fake.posts[0]["url"] == "http://127.0.0.1:11434/api/chat"
    # The counters the bench harness and the logs want, kept as reported.
    assert out.usage == {"eval_count": 12, "eval_duration": 60_000_000, "load_duration": 1_000_000}


def test_thinking_is_off_by_default_and_the_options_carry_the_budget(monkeypatch):
    """`think:false` is the whole reason this path exists; it must be sent even
    when nobody asked, or a reasoning model answers with nothing."""
    router, fake = _router(monkeypatch, FakeResponse(body={"message": {"content": "Rome."}, "done": True}))
    router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.25, 300, None, 30, None)

    body = fake.last
    assert body["think"] is False
    assert body["model"] == "qwen3.5:4b"
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.25, "num_predict": 300}
    assert "keep_alive" not in body and "tools" not in body


def test_options_and_keep_alive_are_passed_straight_through(monkeypatch):
    """`num_gpu: 0` is how a model is put in RAM instead of on the card (D4);
    it has to arrive exactly as the steward wrote it."""
    router, fake = _router(monkeypatch, FakeResponse(body={"message": {"content": "ok"}, "done": True}))
    router.chat(MESSAGES, provider="ollama", model="qwen3.5:4b",
                options={"num_gpu": 0, "temperature": 0.9}, keep_alive="2h", think="low")

    body = fake.last
    assert body["options"]["num_gpu"] == 0
    assert body["options"]["temperature"] == 0.9, "an explicit option wins over the default"
    assert body["options"]["num_predict"] == 768
    assert body["keep_alive"] == "2h"
    assert body["think"] == "low"


def test_other_providers_ignore_the_ollama_extras(monkeypatch):
    """One `chat()` signature for every backend: LM Studio must not be sent
    `think` and must not break because a caller passed it."""
    router, fake = _router(monkeypatch, FakeResponse(body={
        "choices": [{"message": {"content": "Rome."}}],
    }))
    out = router.chat(MESSAGES, provider="lmstudio", model="local-model",
                      think=False, options={"num_gpu": 0}, keep_alive="2h")

    assert out.ok and out.content == "Rome."
    assert fake.posts[0]["url"].endswith("/chat/completions")
    assert "think" not in fake.last and "options" not in fake.last and "keep_alive" not in fake.last


# ───────────────────────── tool calls ─────────────────────────


def test_native_tool_calls_come_back_in_the_usual_shape(monkeypatch):
    router, fake = _router(monkeypatch, FakeResponse(body={
        "message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "open_app", "arguments": {"name": "notepad"}}}],
        },
        "done": True,
    }))
    out = router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.1, 300,
                           [{"type": "function", "function": {"name": "open_app"}}], 30, None)

    assert out.ok and out.tool_calls == [
        {"id": "call_0", "name": "open_app", "arguments": {"name": "notepad"}},
    ]
    assert fake.last["tools"] == [{"type": "function", "function": {"name": "open_app"}}]


def test_a_server_that_rejects_tools_is_asked_again_without_them(monkeypatch):
    router, fake = _router(
        monkeypatch,
        FakeResponse(status=400, text="unsupported: tools"),
        FakeResponse(body={"message": {"content": "I can't do that."}, "done": True}),
    )
    out = router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.1, 300,
                           [{"type": "function", "function": {"name": "open_app"}}], 30, None)

    assert out.ok and out.content == "I can't do that."
    assert "tools" in fake.posts[0]["body"] and "tools" not in fake.posts[1]["body"]


def test_a_model_that_cannot_think_is_asked_again_without_the_key(monkeypatch):
    """Ollama refuses `think` outright for a model with no reasoning mode.
    Asking a plain model *not* to think must never be why a turn fails."""
    router, fake = _router(
        monkeypatch,
        FakeResponse(status=400, text='{"error":"qwen2.5:3b does not support thinking"}'),
        FakeResponse(body={"message": {"content": "Rome."}, "done": True}),
    )
    out = router._chat_one("ollama", MESSAGES, "qwen2.5:3b", 0.3, 200, None, 30, None)

    assert out.ok and out.content == "Rome."
    assert "think" in fake.posts[0]["body"] and "think" not in fake.posts[1]["body"]
    assert len(fake.posts) == 2, "exactly one retry, never a loop"


# ───────────────────────── streaming ─────────────────────────


def test_streaming_calls_on_token_for_every_delta(monkeypatch):
    router, fake = _router(monkeypatch, FakeResponse(lines=[
        _chunk("The capital"),
        _chunk(" of Italy"),
        "",                       # Ollama's keep-alive blank lines
        _chunk(" is Rome."),
        json.dumps({"message": {"content": ""}, "done": True, "eval_count": 9,
                    "eval_duration": 30_000_000}),
    ]))
    seen = []
    out = router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.3, 200, None, 30, seen.append)

    assert out.ok and out.content == "The capital of Italy is Rome."
    assert seen == ["The capital", " of Italy", " is Rome."]
    assert out.usage["eval_count"] == 9
    assert fake.posts[0]["body"]["stream"] is True and fake.posts[0]["stream"] is True


def test_a_stream_carrying_a_tool_call_parses_it_whole(monkeypatch):
    """Unlike the OpenAI protocol, Ollama does not fragment the arguments."""
    router, _fake = _router(monkeypatch, FakeResponse(lines=[
        json.dumps({"message": {"content": "", "tool_calls": [
            {"function": {"name": "close_app", "arguments": {"name": "chrome"}}},
        ]}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]))
    out = router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.1, 300, [{"x": 1}], 30, lambda t: None)
    assert out.tool_calls == [{"id": "call_0", "name": "close_app", "arguments": {"name": "chrome"}}]


def test_cancelling_mid_stream_stops_without_an_error(monkeypatch):
    """Barge-in is not a failure: the partial text comes back flagged, and
    `chat()` must not go looking for another provider."""
    router, _fake = _router(monkeypatch, FakeResponse(lines=[
        _chunk("The capital"), _chunk(" of Italy"), _chunk(" is Rome."),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]))
    seen = []
    stop = {"now": False}

    def on_token(piece):
        seen.append(piece)
        stop["now"] = True     # the user talked over the second chunk

    out = router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.3, 200, None, 30,
                           on_token, should_cancel=lambda: stop["now"])

    assert out.cancelled is True and out.error is None
    assert out.content == "The capital"
    assert seen == ["The capital"], "nothing is generated after the cancel"


def test_an_error_frame_in_the_stream_is_an_error_not_a_reply(monkeypatch):
    router, _fake = _router(monkeypatch, FakeResponse(lines=[
        _chunk("half an ans"),
        json.dumps({"error": "model runner has terminated"}),
    ]))
    out = router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.3, 200, None, 30, lambda t: None)
    assert not out.ok and "model runner" in (out.error or "")


def test_a_dead_ollama_is_reported_not_raised(monkeypatch):
    router = HybridLLMRouter()
    router.ollama_url = "http://127.0.0.1:11434/v1"

    class Dead:
        def post(self, *a, **k):
            raise OSError("connection refused")

    monkeypatch.setattr(router, "session", Dead())
    out = router._chat_one("ollama", MESSAGES, "qwen3.5:4b", 0.3, 200, None, 30, None)
    assert not out.ok and "11434" in (out.error or "")


# ───────────────────────── P1.3: a reply that is only reasoning ─────────────────────────


def test_a_reply_that_is_only_reasoning_is_an_error_so_the_router_escalates(monkeypatch):
    """Measured: qwen3.5 through the OpenAI shim returned empty content and 200
    tokens of `reasoning`. Showing nothing is the worst outcome of the three, so
    it is an error and the turn goes up a rung."""
    router, _fake = _router(monkeypatch, FakeResponse(body={
        "choices": [{"message": {"content": "", "reasoning": "Let me think about Italy. " * 5}}],
    }))
    out = router._chat_one("lmstudio", MESSAGES, "qwen3.5:9b", 0.3, 200, None, 30, None)
    assert not out.ok and out.error == ONLY_THOUGHT


def test_reasoning_alongside_a_real_answer_is_left_alone(monkeypatch):
    router, _fake = _router(monkeypatch, FakeResponse(body={
        "choices": [{"message": {"content": "Rome.", "reasoning_content": "Italy's capital…"}}],
    }))
    out = router._chat_one("lmstudio", MESSAGES, "qwen3.5:9b", 0.3, 200, None, 30, None)
    assert out.ok and out.content == "Rome."


def test_a_tool_call_after_thinking_is_an_answer(monkeypatch):
    router, _fake = _router(monkeypatch, FakeResponse(body={
        "choices": [{"message": {
            "content": "", "reasoning": "the user wants notepad",
            "tool_calls": [{"id": "c1", "function": {"name": "open_app", "arguments": '{"name":"notepad"}'}}],
        }}],
    }))
    out = router._chat_one("lmstudio", MESSAGES, "qwen3.5:9b", 0.1, 300, [{"x": 1}], 30, None)
    assert out.ok and out.tool_calls[0]["name"] == "open_app"


def test_a_stream_that_is_all_thought_is_an_error_too(monkeypatch):
    router, _fake = _router(monkeypatch, FakeResponse(lines=[
        'data: {"choices":[{"delta":{"reasoning":"first I consider Rome"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":", then Milan"}}]}',
        "data: [DONE]",
    ]))
    shown = []
    out = router._chat_one("lmstudio", MESSAGES, "qwen3.5:9b", 0.3, 200, None, 30, shown.append)
    assert not out.ok and out.error == ONLY_THOUGHT
    assert shown == [], "the inner monologue is never shown to the user"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
