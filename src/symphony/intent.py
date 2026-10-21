"""Strict Chat intent proposals and safe file-board filing.

Intent proposals are deliberately process-local.  The transcript is not an
authority store: only an :class:`IntentAction` held by the running server may
be approved by the later Chat integration.

Every proposal is scanned with the sdlc-kit trip-wire heuristics
(``tools/tripwire.sh``: migrations, data deletion, public API, security
paths, infra/config). Hits never block the operator — they are shown on the
card and recorded on the ticket and in ``intent.md`` so the pipeline's Review
lane treats them as mandatory objection candidates — but a hit downgrades a
``micro`` track to ``full``, exactly as the kit's stage 1 requires a
trip-wire-clean intent for the micro track.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast

from .errors import ChatIntentActionError
from .trackers.file import FileBoardTracker
from .workflow.config import TrackerConfig

INTENT_MARKER_OPEN = "<symphony-intent>"
INTENT_MARKER_CLOSE = "</symphony-intent>"
INTENT_ACTION_TTL = timedelta(minutes=30)
MAX_INTENT_ACTIONS = 20
MAX_INTENT_TITLE_LENGTH = 200
MAX_INTENT_BODY_LENGTH = 32 * 1024
MAX_INTENT_SLUG_LENGTH = 64
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
INTENT_ACTION_ID_RE = re.compile(r"^intent-[0-9a-f]{32}$")
_MARKER_RE = re.compile(
    rf"{re.escape(INTENT_MARKER_OPEN)}(?P<body>.*?){re.escape(INTENT_MARKER_CLOSE)}",
    re.DOTALL,
)
IntentStatus = Literal[
    "pending", "running", "approved", "failed", "expired", "superseded"
]
_ALLOWED_KEYS = frozenset({"slug", "title", "track", "intent"})
_REQUIRED_HEADINGS = ("Problem", "Success criteria", "Out of scope")

# sdlc-kit `tools/tripwire.sh` patterns, verbatim (extended regex, case-
# insensitive). A hit is a question for the reviewer, not a conviction.
TRIPWIRE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("migration/schema", r"migrat|schema change|ALTER TABLE|CREATE TABLE|DROP TABLE|[.]sql"),
    ("data deletion", r"DELETE FROM|DROP |TRUNCATE|destructive|backfill|rm -rf"),
    ("public API", r"public API|breaking change|API contract|openapi|swagger|/api/v[0-9]"),
    ("security paths", r"auth|secret|credential|password|token|permission|session"),
    (
        "infra/config",
        r"Dockerfile|docker-compose|[.]github/workflows|terraform|helm|kubernetes|k8s|nginx|systemd|deploy",
    ),
)
_TRIPWIRE_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE)) for label, pattern in TRIPWIRE_PATTERNS
)
TRIPWIRE_MAX_LINES = 3


@dataclass(frozen=True)
class TripwireHit:
    """One trip-wire category that matched, with up to three matching lines."""

    label: str
    lines: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "lines": list(self.lines)}


def scan_tripwires(text: str) -> tuple[TripwireHit, ...]:
    """Heuristic trip-wire scan of an intent body (kit `tools/tripwire.sh`)."""

    hits: list[TripwireHit] = []
    lines = [line.strip() for line in (text or "").splitlines()]
    for label, pattern in _TRIPWIRE_COMPILED:
        matched = [line for line in lines if line and pattern.search(line)]
        if matched:
            hits.append(TripwireHit(label=label, lines=tuple(matched[:TRIPWIRE_MAX_LINES])))
    return tuple(hits)


def render_tripwire_section(hits: tuple[TripwireHit, ...]) -> str:
    """`## Trip-wires` markdown for the ticket body and `intent.md`."""

    if not hits:
        return "## Trip-wires\n\n- none (heuristic scan; the Review lane still judges)\n"
    rows = "\n".join(f"- {hit.label}: `{line}`" for hit in hits for line in hit.lines)
    return (
        "## Trip-wires\n\n"
        "Heuristic hits from the sdlc-kit scan. Each one is a mandatory "
        "objection candidate for the Review lane, not a conviction.\n\n"
        f"{rows}\n"
    )


class IntentParseError(ChatIntentActionError):
    """An agent response did not contain a valid strict intent proposal."""

    code = "chat_intent_parse"


@dataclass(frozen=True)
class IntentProposal:
    slug: str
    title: str
    track: Literal["micro", "full"]
    intent: str
    tripwires: tuple[TripwireHit, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "track": self.track,
            "intent": self.intent,
            "tripwires": [hit.as_dict() for hit in self.tripwires],
        }


@dataclass
class IntentAction:
    """Process-local proposal state owned by a Chat session."""

    action_id: str
    slug: str
    title: str
    track: Literal["micro", "full"]
    intent: str
    expires_at: datetime
    status: IntentStatus = "pending"
    ticket: dict[str, Any] | None = None
    error: str | None = None
    tripwires: tuple[TripwireHit, ...] = ()
    task: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_proposal(
        cls, proposal: IntentProposal, *, now: datetime | None = None
    ) -> "IntentAction":
        created = _utc(now or datetime.now(timezone.utc))
        return cls(
            action_id=f"intent-{uuid.uuid4().hex}",
            slug=proposal.slug,
            title=proposal.title,
            track=proposal.track,
            intent=proposal.intent,
            expires_at=created + INTENT_ACTION_TTL,
            tripwires=proposal.tripwires,
        )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return _utc(now or datetime.now(timezone.utc)) >= _utc(self.expires_at)

    def expire_if_needed(self, *, now: datetime | None = None) -> bool:
        if self.status in {"pending", "failed"} and self.is_expired(now=now):
            self.status = "expired"
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        """Return the client-safe representation (never the in-flight task)."""
        return {
            "action_id": self.action_id,
            "slug": self.slug,
            "title": self.title,
            "track": self.track,
            "intent": self.intent,
            "status": self.status,
            "ticket": self.ticket,
            "error": self.error,
            "expires_at": _utc(self.expires_at).isoformat(),
            "tripwires": [hit.as_dict() for hit in self.tripwires],
        }

    def as_dict(self) -> dict[str, Any]:
        return self.snapshot()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntentParseError("duplicate intent JSON key", key=key)
        result[key] = value
    return result


def _validate_proposal(value: Any) -> IntentProposal:
    if not isinstance(value, dict):
        raise IntentParseError("intent marker must contain a JSON object")
    unknown = set(value) - _ALLOWED_KEYS
    if unknown:
        raise IntentParseError("unknown intent JSON key", key=sorted(unknown)[0])
    missing = _ALLOWED_KEYS - set(value)
    if missing:
        raise IntentParseError("missing intent JSON key", key=sorted(missing)[0])
    slug = value["slug"]
    title = value["title"]
    track = value["track"]
    intent = value["intent"]
    if (
        not isinstance(slug, str)
        or len(slug) > MAX_INTENT_SLUG_LENGTH
        or _SLUG_RE.fullmatch(slug) is None
    ):
        raise IntentParseError("intent slug is not filesystem-safe")
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > MAX_INTENT_TITLE_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in title)
    ):
        raise IntentParseError("intent title is empty or too long")
    if not isinstance(track, str) or track not in {"micro", "full"}:
        raise IntentParseError("intent track must be micro or full")
    if not isinstance(intent, str) or not intent.strip() or len(intent.encode("utf-8")) > MAX_INTENT_BODY_LENGTH:
        raise IntentParseError("intent body is empty or too large")
    body = intent.strip()
    _validate_markdown_contract(body)
    tripwires = scan_tripwires(body)
    if tripwires and track == "micro":
        # Kit stage 1: the micro track requires a trip-wire-clean intent.
        track = "full"
    return IntentProposal(
        slug=slug,
        title=title.strip(),
        track=cast(Literal["micro", "full"], track),
        intent=body,
        tripwires=tripwires,
    )


def _validate_markdown_contract(body: str) -> None:
    headings = set(re.findall(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
    missing = [heading for heading in _REQUIRED_HEADINGS if heading not in headings]
    if missing:
        raise IntentParseError("intent markdown is missing a required heading", heading=missing[0])
    match = re.search(
        r"^##\s+Success criteria\s*$.*?(?=^##\s+|\Z)", body, flags=re.MULTILINE | re.DOTALL
    )
    if match is None or re.search(r"^\s*-\s*\[ \]\s+\S", match.group(0), flags=re.MULTILINE) is None:
        raise IntentParseError("success criteria must include an unchecked checkbox")


def parse_intent_marker(text: str) -> tuple[str, IntentProposal | None]:
    """Remove and parse exactly one intent marker from an agent response.

    The returned text is suitable for display.  A malformed marker raises
    :class:`IntentParseError` and never yields an approvable proposal.
    """
    openings = text.count(INTENT_MARKER_OPEN)
    closings = text.count(INTENT_MARKER_CLOSE)
    matches = list(_MARKER_RE.finditer(text))
    if openings == closings == 0:
        return text, None
    if openings != 1 or closings != 1 or len(matches) != 1:
        raise IntentParseError("exactly one intent marker is required")
    match = matches[0]
    try:
        payload = json.loads(match.group("body"), object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IntentParseError("malformed intent JSON") from exc
    proposal = _validate_proposal(payload)
    visible = (text[: match.start()] + text[match.end() :]).strip()
    return visible, proposal


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _provenance_value(value: str | None) -> str:
    """Keep host-owned provenance bounded and single-line safe."""
    if not value:
        return ""
    clean = "".join(character if ord(character) >= 32 else " " for character in value)
    return clean[:128]


def render_ticket_description(
    action: IntentAction | IntentProposal, *, approved_at: str, session_id: str
) -> str:
    """The request ticket body: the intent plus its track and approval facts."""

    return (
        f"{action.intent}\n\n"
        f"{render_tripwire_section(action.tripwires)}\n"
        f"## Track\n\n{action.track}\n\n"
        "## Approval\n\n"
        f"- Approved at: {approved_at}\n"
        f"- Chat session: {session_id}\n"
        f"- Intent artifact: .sdlc/work/{action.slug}/intent.md\n"
    )


def render_intent_markdown(
    action: IntentAction | IntentProposal,
    *,
    approved_at: str,
    session_id: str,
    ticket_identifier: str,
) -> str:
    """The durable `intent.md` record in the sdlc-kit stage-1 shape."""

    return (
        f"# Intent: {action.slug}\n\n"
        f"- Date: {approved_at[:10]}\n"
        f"- Title: {action.title}\n"
        f"- Track: {action.track}\n"
        f"- Requested by: chat session {session_id}\n\n"
        f"{action.intent}\n\n"
        f"{render_tripwire_section(action.tripwires)}\n"
        "## Approval\n\n"
        f"- Approved at: {approved_at}\n"
        f"- Ticket: {ticket_identifier}\n"
        "- Gate: intent (the only human gate; later stages run unattended)\n"
    )


class IntentFiler:
    """File-board-only filing service used by the Chat approval boundary."""

    def __init__(self, project_root: str | Path, tracker: TrackerConfig) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.tracker_config = tracker

    def file(self, action: IntentAction, *, session_id: str | None = None) -> dict[str, Any]:
        if action.status not in {"running", "pending", "failed"}:
            raise ChatIntentActionError("intent action is not approvable", status=action.status)
        if action.is_expired():
            action.status = "expired"
            raise ChatIntentActionError("intent action has expired")
        if self.tracker_config.kind.lower() != "file" or self.tracker_config.board_root is None:
            raise ChatIntentActionError("intent filing requires a file tracker")
        if not self.tracker_config.active_states:
            raise ChatIntentActionError("intent filing requires an active workflow state")
        if INTENT_ACTION_ID_RE.fullmatch(action.action_id) is None:
            raise ChatIntentActionError("invalid intent action id")
        # Actions normally originate from ``parse_intent_marker``.  Recheck
        # the model at this trust boundary too: callers may construct an
        # action directly, and its title/slug become board/path data.
        try:
            _validate_proposal(
                {
                    "slug": action.slug,
                    "title": action.title,
                    "track": action.track,
                    "intent": action.intent,
                }
            )
        except IntentParseError as exc:
            raise ChatIntentActionError("intent action failed validation") from exc
        # Resolve and validate the artifact destination before allocating a
        # board ticket.  A bad project root or path must never leave behind a
        # request that cannot be accompanied by its approved intent.
        root = (self.project_root / ".sdlc" / "work").resolve()
        if self.project_root != root and self.project_root not in root.parents:
            raise ChatIntentActionError("intent artifact root escaped project root")
        artifact = (root / action.slug / "intent.md").resolve()
        if root != artifact and root not in artifact.parents:
            raise ChatIntentActionError("intent artifact path escaped project root")
        approval_time = datetime.now(timezone.utc).isoformat()
        provenance = (
            "\n\n<!-- Approved by Symphony"
            f" action={_provenance_value(action.action_id)}"
            f" session={_provenance_value(session_id)}"
            f" at={approval_time} -->\n"
        )
        artifact_content = action.intent.rstrip() + provenance
        # The artifact is deterministic for the action (apart from the
        # host-owned approval timestamp), and is deliberately the first
        # mutation.  A failed write therefore cannot leave a board ticket
        # without its supporting intent document.
        _write_text_atomic(artifact, artifact_content)
        if artifact.read_text(encoding="utf-8") != artifact_content:
            raise ChatIntentActionError("intent artifact verification failed")

        board = FileBoardTracker(self.tracker_config)
        state = self.tracker_config.active_states[0]
        ticket_id, ticket_path = board.create_validated(
            identifier=None,
            prefix="REQ",
            title=action.title,
            state=state,
            description=render_ticket_description(
                action, approved_at=approval_time, session_id=session_id or ""
            ),
            request=action.slug,
        )
        return {"id": ticket_id, "identifier": ticket_id, "title": action.title, "path": str(ticket_path), "request": action.slug}


def file_intent_request(
    action: IntentAction,
    *,
    project_root: str | Path,
    tracker: TrackerConfig,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for server-side approval integrations."""
    return IntentFiler(project_root, tracker).file(action, session_id=session_id)
