"""
Gmail and Google Calendar.

This replaces the previous "schedule event" behaviour, which added a row to a
local JSON file and opened the Windows Calendar app without ever creating an
event anywhere.

Setup (one time):
  1. Create a Google Cloud project, enable the Gmail and Calendar APIs.
  2. Create an OAuth client ID of type "Desktop app".
  3. Save the downloaded JSON as  data/google_credentials.json
  4. Ask Dexter to "connect my google account" - a browser window opens once.

The resulting token lives in data/google_token.json, which is gitignored.
Nothing here runs, and no browser opens, until the user explicitly connects.
"""

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from timeparse import humanize, parse_when
from tool_registry import ToolResult, tool

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "google_credentials.json")
TOKEN_FILE = os.path.join(DATA_DIR, "google_token.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]

_services: Dict[str, Any] = {}

SETUP_HINT = (
    "Google isn't connected yet. Put your OAuth desktop credentials at "
    "data/google_credentials.json, then say 'connect my google account'."
)


def _available() -> bool:
    try:
        import google.auth  # noqa: F401
        import googleapiclient  # noqa: F401

        return True
    except Exception:
        return False


def _load_credentials(interactive: bool = False):
    """Return valid credentials, refreshing or prompting only when asked to."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception:
            creds = None

    if not interactive:
        return None

    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(SETUP_HINT)

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def _save_token(creds) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as handle:
        handle.write(creds.to_json())
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass


def _service(name: str, version: str, interactive: bool = False):
    key = f"{name}:{version}"
    if key in _services:
        return _services[key]

    from googleapiclient.discovery import build

    creds = _load_credentials(interactive=interactive)
    if not creds:
        return None
    service = build(name, version, credentials=creds, cache_discovery=False)
    _services[key] = service
    return service


def connected() -> bool:
    return os.path.exists(TOKEN_FILE)


# ───────────────────────── connection ─────────────────────────


@tool(
    name="connect_google",
    description=(
        "Connect the user's Google account so Dexter can read email and manage "
        "their calendar. Opens a browser window for consent - only run when the "
        "user explicitly asks to connect."
    ),
    params={},
    examples=[
        "connect my google account", "link my gmail", "set up google calendar",
        "authorise google", "sign in to google",
    ],
    confirm=True,
    category="google",
)
def connect_google() -> ToolResult:
    if not _available():
        return ToolResult.fail(
            "Google libraries aren't installed. Run: "
            "pip install google-api-python-client google-auth-oauthlib"
        )
    try:
        _services.clear()
        creds = _load_credentials(interactive=True)
        if not creds:
            return ToolResult.fail(SETUP_HINT)
        return ToolResult(ok=True, summary="Google account connected. I can now read your mail and calendar.")
    except FileNotFoundError as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:
        return ToolResult.fail(f"Google sign-in failed: {exc}")


# ───────────────────────── calendar ─────────────────────────


@tool(
    name="create_calendar_event",
    description=(
        "Create a real event in the user's Google Calendar. "
        "Use this whenever they want something actually on their calendar."
    ),
    params={
        "title": {"type": "string", "description": "Event title"},
        "when": {"type": "string", "description": "Start time, e.g. 'tomorrow at 2pm'"},
        "duration_minutes": {
            "type": "integer", "description": "Length in minutes (default 60)", "required": False,
        },
        "description": {"type": "string", "description": "Optional notes", "required": False},
    },
    examples=[
        "put a meeting on my calendar tomorrow at 2",
        "schedule a call with the client on friday at 11",
        "add a calendar event for the dentist next tuesday at 3",
        "book an hour for deep work at 10am",
    ],
    category="google",
)
def create_calendar_event(
    title: str,
    when: str,
    duration_minutes: Optional[int] = None,
    description: Optional[str] = None,
) -> ToolResult:
    service = _service("calendar", "v3")
    if not service:
        return ToolResult.fail(SETUP_HINT)

    start = parse_when(when)
    if not start:
        return ToolResult.fail(f"I couldn't work out when '{when}' is.")
    end = start + timedelta(minutes=duration_minutes or 60)

    tz_name = _local_timezone()
    if tz_name:
        start_body = {"dateTime": start.isoformat(), "timeZone": tz_name}
        end_body = {"dateTime": end.isoformat(), "timeZone": tz_name}
    else:
        # No IANA name available (tzlocal missing) - send explicit UTC offsets
        # instead of a zone name so the times still land correctly.
        start_body = {"dateTime": start.astimezone().isoformat()}
        end_body = {"dateTime": end.astimezone().isoformat()}

    try:
        event = service.events().insert(
            calendarId="primary",
            body={
                "summary": title,
                "description": description or "Created by Dexter",
                "start": start_body,
                "end": end_body,
            },
        ).execute()
        return ToolResult(
            ok=True,
            summary=f"Added '{title}' to your calendar {humanize(start)}.",
            data={"id": event.get("id"), "link": event.get("htmlLink"),
                  "start": start.isoformat()},
        )
    except Exception as exc:
        return ToolResult.fail(f"Could not create the event: {exc}")


@tool(
    name="list_calendar_events",
    description="List upcoming events from the user's Google Calendar.",
    params={
        "days": {"type": "integer", "description": "How many days ahead (default 1)", "required": False},
    },
    examples=[
        "what's on my calendar", "what meetings do I have today",
        "what's on my calendar tomorrow", "any events this week",
    ],
    category="google",
)
def list_calendar_events(days: Optional[int] = None) -> ToolResult:
    service = _service("calendar", "v3")
    if not service:
        return ToolResult.fail(SETUP_HINT)

    now = datetime.now()
    horizon = now + timedelta(days=days or 1)
    try:
        result = service.events().list(
            calendarId="primary",
            timeMin=now.astimezone().isoformat(),
            timeMax=horizon.astimezone().isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()
        events = result.get("items", [])
        if not events:
            window = "today" if not days or days <= 1 else f"the next {days} days"
            return ToolResult(ok=True, summary=f"Nothing on your calendar for {window}.",
                              data={"events": []})

        lines = []
        for event in events[:8]:
            start = event["start"].get("dateTime") or event["start"].get("date", "")
            try:
                label = datetime.fromisoformat(start).strftime("%I:%M %p").lstrip("0")
            except Exception:
                label = start
            lines.append(f"{label} {event.get('summary', '(no title)')}")
        return ToolResult(ok=True, summary=f"{len(events)} event(s): " + "; ".join(lines),
                          data={"events": events})
    except Exception as exc:
        return ToolResult.fail(f"Could not read your calendar: {exc}")


def _local_timezone() -> Optional[str]:
    """
    The Calendar API expects an IANA zone like 'Asia/Kolkata'. tzname() returns
    abbreviations like 'IST', which the API rejects or - worse - maps to a
    different zone, silently shifting event times.
    """
    try:
        from tzlocal import get_localzone_name
        return get_localzone_name() or None
    except Exception:
        return None


# ───────────────────────── gmail ─────────────────────────


def _header(payload: Dict[str, Any], name: str) -> str:
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


@tool(
    name="check_email",
    description=(
        "Check the user's Gmail inbox and summarise recent or unread messages."
    ),
    params={
        "query": {
            "type": "string",
            "description": "Gmail search, e.g. 'is:unread', 'from:boss'. Defaults to unread.",
            "required": False,
        },
        "count": {"type": "integer", "description": "How many to fetch", "required": False},
    },
    examples=[
        "check my email", "any new emails", "what's in my inbox",
        "do I have unread mail", "summarise my inbox", "any email from the client",
    ],
    category="google",
)
def check_email(query: Optional[str] = None, count: Optional[int] = None) -> ToolResult:
    service = _service("gmail", "v1")
    if not service:
        return ToolResult.fail(SETUP_HINT)

    try:
        listing = service.users().messages().list(
            userId="me", q=query or "is:unread", maxResults=count or 5,
        ).execute()
        messages = listing.get("messages", [])
        if not messages:
            return ToolResult(ok=True, summary="No matching emails - your inbox is clear.",
                              data={"emails": []})

        summaries = []
        for stub in messages:
            detail = service.users().messages().get(
                userId="me", id=stub["id"], format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
            payload = detail.get("payload", {})
            sender = _header(payload, "From").split("<")[0].strip().strip('"')
            summaries.append({
                "id": stub["id"],
                "from": sender,
                "subject": _header(payload, "Subject"),
                "snippet": detail.get("snippet", ""),
            })

        preview = "; ".join(f"{e['from']}: {e['subject']}" for e in summaries[:5])
        return ToolResult(ok=True, summary=f"{len(summaries)} email(s). {preview}",
                          data={"emails": summaries})
    except Exception as exc:
        return ToolResult.fail(f"Could not read your email: {exc}")


@tool(
    name="send_email",
    description="Send an email from the user's Gmail account.",
    params={
        "to": {"type": "string", "description": "Recipient email address"},
        "subject": {"type": "string", "description": "Subject line"},
        "body": {"type": "string", "description": "Message body"},
    },
    examples=["send an email to", "email the client", "reply to that message"],
    confirm=True,
    category="google",
)
def send_email(to: str, subject: str, body: str) -> ToolResult:
    service = _service("gmail", "v1")
    if not service:
        return ToolResult.fail(SETUP_HINT)

    import base64
    from email.message import EmailMessage

    try:
        message = EmailMessage()
        message.set_content(body)
        message["To"] = to
        message["Subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return ToolResult(ok=True, summary=f"Email sent to {to}.", data={"to": to})
    except Exception as exc:
        return ToolResult.fail(f"Could not send the email: {exc}")


def availability() -> Dict[str, Any]:
    return {
        "librariesInstalled": _available(),
        "credentialsPresent": os.path.exists(CREDENTIALS_FILE),
        "connected": connected(),
    }
