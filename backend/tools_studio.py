"""
The Buzzcaf Studio, as Dexter tools (roadmap v5, 3.2).

Everything Dexter can do to the Studio lives here: start it, list and create
projects, run and approve workflow steps, ask a channel strategist, browse and
save topics, list personas. Each tool is one HTTP call through
buzzcaf_client.py against the control contract in ECOSYSTEM.md, and each one
reports failures as failures - the old `buzzcaf_execute_step` said "Ran next
step" whatever the Studio answered.
"""

from typing import Any, Dict, List, Optional

from buzzcaf_client import buzzcaf_client
from tool_registry import ToolResult, tool

# Channel names as the Studio spells them, keyed by the words people say.
_CHANNEL_HINTS = (
    (("after dark", "afterdark", "horror", "raat", "ghost"), "Spilled Coffee After Dark"),
    (("coffee studio", "spilled coffee studio", "originals"), "Spilled Coffee Studio"),
    (("beyond", "documentary", "true crime", "true story"), "Beyond3Baje"),
    (("life3", "life 3", "life baje", "essay", "vlog"), "Life3Baje"),
    (("khayal", "mythology", "lore", "epic"), "Khayal3Baje"),
)


# The Studio's default workflow per channel (backend/prompts/workflows/*.json).
_WORKFLOW_FOR = {
    "Spilled Coffee After Dark": "after_dark_narration",
    "Spilled Coffee Studio": "spilled_coffee_story",
    "Beyond3Baje": "beyond3baje_documentary",
    "Life3Baje": "life3baje_video",
    "Khayal3Baje": "khayal3baje_horror",
}


def channel_from_text(text: Optional[str]) -> Optional[str]:
    lower = (text or "").lower()
    for needles, channel in _CHANNEL_HINTS:
        if any(n in lower for n in needles):
            return channel
    return None


def _default_channel(channel: Optional[str], hint_text: str = "") -> str:
    if channel:
        return channel_from_text(channel) or channel
    found = channel_from_text(hint_text)
    if found:
        return found
    active = buzzcaf_client.get_active_project_details()
    if active and active.get("brand"):
        return active["brand"]
    return "Beyond3Baje"


def _last_step(project: Dict[str, Any]) -> Dict[str, Any]:
    history = project.get("steps_history") or project.get("history") or []
    return history[-1] if isinstance(history, list) and history else {}


def _offline(raw: Any = None) -> ToolResult:
    return ToolResult.fail(
        "The Buzzcaf Studio is offline or did not answer. Say 'open the studio' to start it.",
        raw=raw,
    )


# ───────────────────────── status / launch ─────────────────────────


@tool(
    name="buzzcaf_status",
    description="Check whether the Buzzcaf Studio is running and summarise its projects and pending approvals.",
    params={},
    examples=[
        "what's the status of buzzcaf", "buzzcaf project status", "studio status",
        "how are my video projects doing", "is the studio running", "is buzzcaf up",
    ],
    category="buzzcaf",
)
def buzzcaf_status() -> ToolResult:
    if not buzzcaf_client.health_check(force=True):
        return ToolResult(
            ok=True,
            summary="The Buzzcaf Studio is offline. Say 'open the studio' to bring it up.",
            data={"online": False},
        )
    state = buzzcaf_client.state()
    if state.get("error"):
        return _offline(state)
    counts = state.get("projects") or {}
    pending = state.get("pending_approvals") or []
    summary = (
        f"The Studio is online with {counts.get('total', 0)} project(s), "
        f"{counts.get('in_progress', 0)} in progress"
    )
    if pending:
        summary += f", {len(pending)} waiting for your approval"
    active = state.get("active_project")
    if active:
        summary += f". Active: '{active.get('name')}' at {active.get('current_step')}"
    return ToolResult(ok=True, summary=summary + ".", data=state)


@tool(
    name="buzzcaf_start_studio",
    description="Open the Buzzcaf Studio desktop app (its backend and window).",
    params={},
    examples=[
        "start buzzcaf studio", "launch buzzcafai", "open the studio", "open buzzcaf",
        "boot up the agent studio", "start the studio app",
    ],
    category="buzzcaf",
)
def buzzcaf_start_studio() -> ToolResult:
    result = buzzcaf_client.start_studio(wait_for_ready=True, timeout=30)
    online = result.get("ready") or (result.get("backend") or {}).get("already_running")
    return ToolResult(
        ok=bool(online),
        summary=(
            "The Buzzcaf Studio is up." if online
            else "I tried to start the Buzzcaf Studio but it did not come online in time."
        ),
        data=result,
    )


# ───────────────────────── projects ─────────────────────────


@tool(
    name="buzzcaf_list_projects",
    description="List the video projects in the Buzzcaf Studio.",
    params={},
    examples=["show my video projects", "list buzzcaf projects", "what projects do I have", "list studio projects"],
    category="buzzcaf",
)
def buzzcaf_list_projects() -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    projects = buzzcaf_client.list_projects() or []
    if not projects:
        return ToolResult(ok=True, summary="You have no Studio projects yet.", data={"projects": []})
    names = "; ".join(
        f"{p.get('name', 'untitled')} ({p.get('brand', '?')}, {p.get('current_step') or p.get('status', 'unknown')})"
        for p in projects[:5]
    )
    return ToolResult(ok=True, summary=f"{len(projects)} project(s): {names}", data={"projects": projects})


@tool(
    name="buzzcaf_create_project",
    description="Create a new video production project in the Buzzcaf Studio for a channel.",
    params={
        "name": {"type": "string", "description": "Project or video topic name"},
        "brand": {"type": "string", "description": "Channel brand", "required": False},
        "workflow_name": {"type": "string", "description": "Workflow template", "required": False},
    },
    examples=[
        "start a new video about AI agents", "create a video project on productivity",
        "make a video about the new iphone", "produce a video on crypto trends",
        "start a video about bhangarh fort on beyond3baje",
    ],
    category="buzzcaf",
)
def buzzcaf_create_project(
    name: str, brand: Optional[str] = None, workflow_name: Optional[str] = None
) -> ToolResult:
    if not buzzcaf_client.health_check():
        buzzcaf_client.start_studio(wait_for_ready=True, timeout=30)

    brands = buzzcaf_client.list_brands()
    if not brands:
        return ToolResult.fail(
            "The Studio returned no channel brands - is it running? Say 'open the studio' first."
        )
    wanted = channel_from_text(brand) or channel_from_text(name)
    chosen = wanted if wanted in brands else (brand if brand in brands else brands[0])

    if not workflow_name:
        workflows = buzzcaf_client.list_workflows() or []
        ids = [w.get("id") or w.get("name") for w in workflows]
        preferred = _WORKFLOW_FOR.get(chosen)
        if preferred and (not ids or preferred in ids):
            workflow_name = preferred
        else:
            workflow_name = next((w for w in ids if w and "documentary" in w), None) or (
                ids[0] if ids else "beyond3baje_documentary"
            )

    project = buzzcaf_client.create_project(name, chosen, workflow_name)
    if not project or not project.get("id"):
        return ToolResult.fail("Could not create the project in the Studio.", raw=project)

    buzzcaf_client.set_active_project(project["id"])
    return ToolResult(
        ok=True,
        summary=(
            f"Created '{name}' on {chosen} (workflow {workflow_name}); first step is "
            f"{project.get('current_step', 'the first step')}. Say 'run the next step' to begin."
        ),
        data=project,
    )


def _resolve_project(name_or_id: Optional[str], prefer_pending: bool = False) -> Optional[str]:
    """A project id from a name fragment, the pending list, or the active project."""
    projects = buzzcaf_client.list_projects() or []
    if name_or_id:
        needle = name_or_id.lower().strip()
        for p in projects:
            if needle == str(p.get("id", "")).lower() or needle in str(p.get("name", "")).lower():
                return p.get("id")
    if prefer_pending:
        for p in projects:
            if _last_step(p).get("status") == "paused_for_approval":
                return p.get("id")
    active = buzzcaf_client.get_active_project()
    if active:
        return active
    for p in projects:
        if p.get("status") not in ("completed", "archived"):
            return p.get("id")
    return None


@tool(
    name="buzzcaf_execute_step",
    description=(
        "Run the next workflow step on the active Studio video project, "
        "optionally with revision feedback."
    ),
    params={
        "feedback": {
            "type": "string",
            "description": "Optional revision notes for the current step",
            "required": False,
        }
    },
    examples=[
        "run the next step", "continue the project", "advance the workflow",
        "make the script funnier", "revise that and continue", "run the next studio step",
    ],
    category="buzzcaf",
)
def buzzcaf_execute_step(feedback: Optional[str] = None) -> ToolResult:
    project_id = _resolve_project(None)
    if not project_id:
        return ToolResult.fail("There is no active video project. Say 'start a video about ...' first.")
    buzzcaf_client.set_active_project(project_id)

    result = buzzcaf_client.execute_step(project_id, feedback)
    if not isinstance(result, dict):
        return ToolResult.fail("The Studio returned an unexpected reply for that step.", raw=result)
    if result.get("error") or result.get("status") == "error":
        detail = result.get("error") or result.get("message") or "unknown error"
        return ToolResult.fail(f"The Studio could not run the step: {detail}", raw=result)

    last = _last_step(result)
    step = last.get("step_name") or result.get("current_step") or "the next step"
    step_status = last.get("status") or ""
    if step_status == "paused_for_approval":
        summary = (
            f"{step} finished and is waiting for your approval. "
            "Say 'approve' to continue or give revision notes."
        )
    elif step_status in ("failed", "error"):
        return ToolResult.fail(f"{step} failed in the Studio.", raw=result)
    elif result.get("status") == "completed":
        summary = f"{step} finished; the project is complete."
    else:
        summary = f"Ran {step}; the project is now at {result.get('current_step')}."
    return ToolResult(ok=True, summary=summary, data=result)


@tool(
    name="studio_pending_approvals",
    description="List the Studio workflow steps that are waiting for the creator's approval.",
    params={},
    examples=[
        "anything waiting on me in the studio", "what needs my approval",
        "pending approvals in buzzcaf", "is the studio waiting for me",
        "what is stuck in the studio", "do I need to approve anything",
    ],
    category="buzzcaf",
)
def studio_pending_approvals() -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    state = buzzcaf_client.state()
    if state.get("error"):
        return _offline(state)
    pending = state.get("pending_approvals") or []
    if not pending:
        return ToolResult(ok=True, summary="Nothing is waiting for your approval in the Studio.", data=state)
    lines = "; ".join(f"'{p.get('name')}' ({p.get('brand')}) at {p.get('step')}" for p in pending[:5])
    return ToolResult(
        ok=True,
        summary=f"{len(pending)} step(s) waiting for approval: {lines}. Say 'approve' to continue.",
        data={"pending": pending},
    )


@tool(
    name="studio_approve_step",
    description=(
        "Approve the Studio step that is waiting for the creator, or send it back with revision notes."
    ),
    params={
        "project_name": {
            "type": "string",
            "description": "Which project (name fragment or id); defaults to the one waiting",
            "required": False,
        },
        "feedback": {
            "type": "string",
            "description": "Revision notes instead of a plain approval",
            "required": False,
        },
    },
    examples=[
        "approve", "approve that step", "approve the step", "looks good, continue",
        "approve the research step", "approve the studio step",
        "send it back with notes: make it shorter",
    ],
    category="buzzcaf",
)
def studio_approve_step(project_name: Optional[str] = None, feedback: Optional[str] = None) -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    project_id = _resolve_project(project_name, prefer_pending=True)
    if not project_id:
        return ToolResult.fail("I could not find a Studio project to approve.")
    result = buzzcaf_client.approve(project_id, feedback)
    if not isinstance(result, dict):
        return ToolResult.fail("The Studio returned an unexpected reply.", raw=result)
    if result.get("error"):
        code = result.get("status_code")
        if code == 409:
            return ToolResult.fail(f"Nothing is waiting for approval: {result['error']}", raw=result)
        return ToolResult.fail(f"The Studio could not approve the step: {result['error']}", raw=result)
    buzzcaf_client.set_active_project(project_id)
    last = _last_step(result)
    if feedback:
        summary = f"Sent '{last.get('step_name') or 'the step'}' back with your notes on '{result.get('name')}'."
        if last.get("status") == "paused_for_approval":
            summary += " It has been redone and is waiting for approval again."
    elif result.get("status") == "completed":
        summary = f"Approved; '{result.get('name')}' is complete."
    else:
        summary = f"Approved; '{result.get('name')}' moved on to {result.get('current_step')}."
    return ToolResult(ok=True, summary=summary, data=result)


# ───────────────────────── strategist chat / topics / agents ─────────────────────────


@tool(
    name="studio_chat",
    description=(
        "Ask the Studio's channel strategist for content advice: hooks, titles, outlines, "
        "structure, research angles for one of the YouTube channels."
    ),
    params={
        "question": {"type": "string", "description": "What to ask the strategist"},
        "channel": {"type": "string", "description": "Channel brand", "required": False},
    },
    examples=[
        "ask the beyond3baje strategist for three hooks about bhangarh fort",
        "ask the studio for title ideas for khayal3baje",
        "what does the studio strategist think about this topic",
        "get hook ideas from the studio", "ask the after dark strategist for a scary opening",
        "ask the studio how to structure this video",
    ],
    category="buzzcaf",
)
def studio_chat(question: str, channel: Optional[str] = None) -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    chosen = _default_channel(channel, question)
    result = buzzcaf_client.chat(question, channel=chosen)
    if not isinstance(result, dict) or result.get("error") or result.get("status") == "error":
        detail = (result or {}).get("detail") or (result or {}).get("error") or "no reply"
        return ToolResult.fail(f"The Studio strategist could not answer: {detail}", raw=result)
    reply = str(result.get("reply") or "").strip()
    label = f"{result.get('agent_name', 'The strategist')} ({chosen})"
    note = " [simulated - no model answered]" if result.get("simulated") else ""
    return ToolResult(ok=True, summary=f"{label}{note}: {reply[:1500]}", data=result)


@tool(
    name="studio_discover_topics",
    description="Show the Studio's curated topic ideas for a channel (a seed list, not a live search).",
    params={"channel": {"type": "string", "description": "Channel brand", "required": False}},
    examples=[
        "what topics does the studio suggest for life3baje", "topic ideas from the studio",
        "show studio topic ideas for beyond3baje", "discover topics in the studio",
        "what should khayal3baje make next according to the studio",
    ],
    category="buzzcaf",
)
def studio_discover_topics(channel: Optional[str] = None) -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    chosen = _default_channel(channel)
    result = buzzcaf_client.discover_topics(chosen)
    if not isinstance(result, dict) or result.get("error"):
        return ToolResult.fail("The Studio could not list topic ideas.", raw=result)
    topics = result.get("topics") or []
    if not topics:
        return ToolResult(ok=True, summary=f"The Studio has no topic ideas for {chosen}.", data=result)
    titles = "; ".join(str(t.get("topic", "")) for t in topics[:5])
    source = " (curated seed list)" if result.get("source") == "curated_seed" else ""
    return ToolResult(
        ok=True,
        summary=f"{len(topics)} topic idea(s) for {chosen}{source}: {titles}",
        data=result,
    )


@tool(
    name="studio_saved_topics",
    description="List the topics saved in the Studio's vault, optionally for one channel.",
    params={"channel": {"type": "string", "description": "Channel brand", "required": False}},
    examples=[
        "show saved topics for khayal3baje", "what topics are saved in the studio vault",
        "list the studio vault", "saved topics in buzzcaf", "what is in the topic vault",
    ],
    category="buzzcaf",
)
def studio_saved_topics(channel: Optional[str] = None) -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    chosen = channel_from_text(channel) or channel
    topics = buzzcaf_client.saved_topics(chosen)
    if not topics:
        where = f" for {chosen}" if chosen else ""
        return ToolResult(ok=True, summary=f"The Studio vault has no saved topics{where}.", data={"topics": []})
    titles = "; ".join(f"{t.get('topic')} ({t.get('channel')})" for t in topics[:6])
    return ToolResult(ok=True, summary=f"{len(topics)} saved topic(s): {titles}", data={"topics": topics})


@tool(
    name="studio_save_topic",
    description="Save a topic idea into the Studio's vault for a channel.",
    params={
        "topic": {"type": "string", "description": "The topic title to save"},
        "channel": {"type": "string", "description": "Channel brand", "required": False},
    },
    examples=[
        "save that as a topic for beyond3baje", "save this topic to the studio vault",
        "add bhangarh fort to the studio vault", "bookmark this topic in the studio",
        "save a topic about the last lantern for khayal3baje",
    ],
    category="buzzcaf",
)
def studio_save_topic(topic: str, channel: Optional[str] = None) -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    chosen = _default_channel(channel, topic)
    result = buzzcaf_client.save_topic({"topic": topic, "channel": chosen, "category": "From Dexter"})
    if not isinstance(result, dict) or result.get("error"):
        return ToolResult.fail("The Studio could not save that topic.", raw=result)
    return ToolResult(ok=True, summary=f"Saved '{topic}' to the Studio vault for {chosen}.", data=result)


@tool(
    name="studio_agents",
    description="List the specialist agent personas the Studio has.",
    params={},
    examples=[
        "which agents does the studio have", "list the studio personas",
        "how many agents are in buzzcaf", "what specialists does the studio have",
    ],
    category="buzzcaf",
)
def studio_agents() -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    agents = buzzcaf_client.list_agents()
    if not agents:
        return ToolResult.fail("The Studio returned no agents.")
    names = ", ".join(a.get("name", "") for a in agents[:12])
    return ToolResult(
        ok=True,
        summary=f"The Studio has {len(agents)} agent personas, for example {names}.",
        data={"agents": agents},
    )


# ───────────────────────── YouTube status via BuzzBrain ─────────────────────────


def _fmt_num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(int(number))


def _describe_video(record: Dict[str, Any]) -> str:
    bits = [f"'{record.get('title') or record.get('video_id')}' ({record.get('channel_name') or '?'})"]
    if record.get("views") is not None:
        bits.append(f"{_fmt_num(record['views'])} views")
    if record.get("vph"):
        bits.append(f"{_fmt_num(record['vph'])} views/hour")
    if record.get("likes") is not None:
        bits.append(f"{_fmt_num(record['likes'])} likes")
    if record.get("published_ago"):
        bits.append(f"published {record['published_ago']}")
    if record.get("outlier"):
        bits.append(str(record["outlier"]))
    when = str(record.get("captured_at") or "")[:16].replace("T", " ")
    return ", ".join(bits) + (f" (seen {when})" if when else "")


@tool(
    name="youtube_channel_status",
    description=(
        "How the owner's YouTube channels are doing, from the BuzzBrain snapshots the Studio keeps: "
        "subscribers, videos seen, best views-per-hour, the latest video."
    ),
    params={},
    examples=[
        "how are my channels doing", "youtube channel status", "how is my youtube doing",
        "what does buzzbrain say about my channels", "channel stats from buzzbrain",
    ],
    category="buzzcaf",
)
def youtube_channel_status() -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    data = buzzcaf_client.buzzbrain_channels()
    if data.get("error"):
        return _offline(data)
    channels = data.get("channels") or []
    if not channels:
        return ToolResult(
            ok=True,
            summary=(
                "No BuzzBrain snapshots yet. Open one of your videos on YouTube with the "
                "BuzzBrain extension installed and it will start reporting."
            ),
            data=data,
        )
    mine = [c for c in channels if c.get("mine")]
    lines = []
    for channel in (mine or channels)[:5]:
        latest = channel.get("latest_video") or {}
        line = f"{channel.get('name') or channel.get('key')}: {_fmt_num(channel.get('subscribers'))} subscribers, {channel.get('videos_seen', 0)} video(s) seen"
        if channel.get("best_vph"):
            line += f", best {_fmt_num(channel['best_vph'])} views/hour"
        if latest:
            line += f"; latest {_describe_video(latest)}"
        lines.append(line)
    prefix = "Your channels" if mine else "Channels seen (none marked as yours yet)"
    return ToolResult(ok=True, summary=f"{prefix}: " + " | ".join(lines), data=data)


@tool(
    name="youtube_video_stats",
    description=(
        "Stats for the owner's latest YouTube video (or a named one) from BuzzBrain: views, "
        "views per hour, likes, and how the view count moved across snapshots."
    ),
    params={
        "video": {
            "type": "string",
            "description": "A video id or part of the title; defaults to the latest of your videos",
            "required": False,
        }
    },
    examples=[
        "how is my latest video doing", "views on my last video", "how did the bhangarh video do",
        "latest video stats", "what are the numbers on my new upload", "video performance from buzzbrain",
    ],
    category="buzzcaf",
)
def youtube_video_stats(video: Optional[str] = None) -> ToolResult:
    if not buzzcaf_client.health_check():
        return _offline()
    latest = buzzcaf_client.buzzbrain_latest(n=25)
    if latest.get("error"):
        return _offline(latest)
    videos = latest.get("videos") or []
    if not videos:
        return ToolResult(ok=True, summary="No BuzzBrain snapshots yet, so I have no video numbers.", data=latest)
    chosen = None
    if video:
        needle = video.lower().strip()
        chosen = next((v for v in videos if needle == str(v.get("video_id", "")).lower()
                       or needle in str(v.get("title", "")).lower()), None)
        if not chosen:
            return ToolResult.fail(f"I have no snapshot of a video matching '{video}'.")
    else:
        chosen = next((v for v in videos if v.get("mine")), videos[0])
    detail = buzzcaf_client.buzzbrain_video(str(chosen.get("video_id")))
    history = detail.get("history") or [] if isinstance(detail, dict) else []
    summary = _describe_video(chosen)
    if len(history) >= 2:
        first, last = history[0], history[-1]
        if first.get("views") is not None and last.get("views") is not None:
            summary += (
                f". Views moved {_fmt_num(first['views'])} -> {_fmt_num(last['views'])} "
                f"across {len(history)} snapshots"
            )
    if not chosen.get("mine"):
        summary += " (not marked as one of your channels)"
    return ToolResult(ok=True, summary=summary + ".", data={"latest": chosen, "history": history})


@tool(
    name="studio_analyze_video",
    description=(
        "Ask the Studio to analyse a YouTube video or channel link: why it performs (hook, packaging, "
        "most-replayed moments, outlier factor against the channel's recent uploads) and a blueprint for "
        "making the user's own video after it, tailored to one of the user's channels."
    ),
    params={
        "url": {"type": "string", "description": "YouTube video or channel URL"},
        "channel": {"type": "string", "description": "Which of the user's channels to model it for", "required": False},
    },
    examples=[
        "analyse this video https://www.youtube.com/watch?v=abc123 for beyond3baje",
        "why is this video doing so well https://youtu.be/abc123",
        "break down this channel https://www.youtube.com/@somecreator",
        "study https://www.youtube.com/watch?v=abc123 and tell me how to model my video on it",
        "what makes this one work https://www.youtube.com/watch?v=abc123",
    ],
    category="studio",
)
def studio_analyze_video(url: str, channel: Optional[str] = None) -> ToolResult:
    url = (url or "").strip()
    if not url.lower().startswith("http"):
        return ToolResult.fail("Give me the YouTube link to analyse.")
    if not buzzcaf_client.ensure_running(auto_start=True, timeout=30):
        return ToolResult.fail("The Studio is not running and could not be started.")
    target_channel = channel_from_text(channel) if channel else None
    result = buzzcaf_client.analyze_video(url, target_channel)
    if not isinstance(result, dict) or result.get("error"):
        detail = result.get("error") if isinstance(result, dict) else str(result)
        return ToolResult.fail(f"The Studio could not analyse that link: {detail}")
    analysis = result.get("analysis") or {}
    video = result.get("video") or {}
    metrics = result.get("metrics") or {}
    if result.get("kind") == "channel":
        patterns = analysis.get("what_works") or analysis.get("why_it_works") or []
        heads = "; ".join(str(p.get("factor") if isinstance(p, dict) else p) for p in patterns[:3])
        summary = (
            f"{video.get('channel') or 'That channel'}: {metrics.get('videos_sampled', 0)} recent videos, "
            f"median {metrics.get('median_views', 0):,} views. What works: {heads or 'see the Studio'}."
        )
    else:
        factors = analysis.get("why_it_works") or []
        heads = "; ".join(str(f.get("factor") if isinstance(f, dict) else f) for f in factors[:3])
        titles = (analysis.get("blueprint") or {}).get("working_titles") or []
        outlier = metrics.get("outlier_multiple")
        outlier_text = f", {outlier}x the channel's median" if outlier else ""
        summary = (
            f"'{video.get('title')}' ({video.get('view_count', 0):,} views{outlier_text}). "
            f"Why it works: {heads or 'see the Studio'}."
        )
        if titles:
            summary += f" A title for you: {titles[0]}"
    if result.get("simulated"):
        summary += " (No model was reachable, so this is the metrics-only read, not a model analysis.)"
    summary += " Full breakdown is in the Studio's Analyze tab."
    return ToolResult(ok=True, summary=summary, data=result)
