"""MastGaane as Dexter's music: routing, the remote, and the media-key handover."""

import pytest

import router
import tools_music


class FakeMastGaane:
    """Stands in for MastGaane's /api/player routes."""

    def __init__(self, state="playing"):
        self.state = state
        self.calls = []

    def request(self, method, url, json=None, timeout=None):
        route = url.rsplit("/api/player/", 1)[1]
        self.calls.append((method, route, json))
        if route in ("pause",):
            self.state = "paused"
        elif route in ("resume", "play"):
            self.state = "playing"
        elif route == "stop":
            self.state = "stopped"
        body = {"state": self.state, "focus": (json or {}).get("focus", ""), "summary": f"state {self.state}"}
        return _Response(body)


class _Response:
    status_code = 200
    reason = "OK"

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.fixture()
def mg(monkeypatch):
    import requests

    fake = FakeMastGaane()
    monkeypatch.setattr(tools_music, "_port", lambda: 8788)
    monkeypatch.setattr(requests, "request", fake.request)
    return fake


@pytest.mark.parametrize("said, tool, args", [
    ("play some ghazals", "mastgaane_play", {"focus": "ghazals"}),
    ("play hawa mahal", "mastgaane_play", {"focus": "hawa mahal"}),
    ("put on some purane gaane", "mastgaane_play", {"focus": "purane gaane"}),
    ("play songs by jagjit singh", "mastgaane_play", {"focus": "songs by jagjit singh"}),
    ("play jaimala on mastgaane", "mastgaane_play", {"focus": "jaimala"}),
    ("start mastgaane fm", "mastgaane_play", {"focus": ""}),
    ("play some music", "mastgaane_play", {"focus": ""}),
    ("i like this song", "mastgaane_feedback", {"verdict": "like"}),
    ("never play this song again", "mastgaane_feedback", {"verdict": "dislike"}),
])
def test_music_phrases_reach_mastgaane(said, tool, args):
    assert router.fast_path(said) == (tool, args)


@pytest.mark.parametrize("said", ["play some lofi music", "put on some jazz", "play the next song",
                                  "play the second video", "start mastgaane", "like this video"])
def test_other_phrases_are_left_alone(said):
    hit = router.fast_path(said)
    assert hit is None or not hit[0].startswith("mastgaane_")


def test_play_starts_with_the_wish(mg):
    res = tools_music.mastgaane_play("ghazals")
    assert res.ok and res.summary == "Starting MastGaane — ghazals."
    assert mg.calls == [("POST", "play", {"focus": "ghazals"})]


def test_play_starts_mastgaane_when_it_is_not_running(monkeypatch, mg):
    import tools_apps

    ports = iter([None, None, 8788])
    monkeypatch.setattr(tools_music, "_port", lambda: next(ports))
    monkeypatch.setattr(tools_music.time, "sleep", lambda s: None)
    started = []
    monkeypatch.setattr(tools_apps, "open_app", lambda app: started.append(app) or tools_music.ToolResult(ok=True, summary="ok", data={}))
    assert tools_music.mastgaane_play("").ok
    assert started == ["mastgaane"]


def test_media_keys_hand_over_while_mastgaane_plays(mg):
    import media_tools

    assert media_tools.media_control("pause").ok and mg.state == "paused"
    assert media_tools.media_control("playpause").ok and mg.state == "playing"
    assert media_tools.media_control("next").ok and mg.calls[-1][1] == "next"
    assert not tools_music.take_over("previous").ok
    assert media_tools.get_now_playing().summary == "state playing"


def test_nothing_is_taken_over_when_mastgaane_is_quiet(monkeypatch, mg):
    mg.state = "stopped"
    assert tools_music.take_over("pause") is None
    assert tools_music.now_playing() is None
    assert not tools_music.mastgaane_feedback("like").ok
    monkeypatch.setattr(tools_music, "_port", lambda: None)
    assert tools_music.active() is None


def test_feedback(mg):
    assert tools_music.mastgaane_feedback("like").summary == "Noted — more like this."
    assert tools_music.mastgaane_feedback("dislike").summary.startswith("Got it, never again")
    assert [c[1] for c in mg.calls if c[0] == "POST"] == ["like", "dislike"]
