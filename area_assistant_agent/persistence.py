"""Durable, document-isolated storage for local Agent sessions."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional, Tuple
import uuid


DATA_DIRECTORY_NAME = "AI_Area_Assistant_Data"
STATE_FORMAT_VERSION = 1
REDACTED = "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    collapsed = normalized.replace("_", "")
    return (
        collapsed
        in {
            "authorization",
            "apikey",
            "accesstoken",
            "refreshtoken",
            "sessiontoken",
            "idtoken",
            "token",
            "clientsecret",
            "secret",
            "secretkey",
            "password",
        }
        or normalized.endswith(("_api_key", "_token", "_secret", "_password"))
    )


def _redact_text(value: str) -> str:
    value = re.sub(
        r'(?i)(["\']?)(authorization|api[-_ ]?key|token|secret|password)\1'
        r'\s*[:=]\s*(["\']?)(?:(?:Bearer|Basic)\s+)?[^\s,;"\'}]+\3',
        lambda match: match.group(2) + "=" + REDACTED,
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+[^\s,;]+", REDACTED, value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{6,}\b", REDACTED, value)
    return value


def redact_sensitive(value: Any) -> Any:
    """Return a JSON-compatible copy with credential-shaped values removed."""
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive_key(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary_path), str(path))


def _append_jsonl(path: Path, value: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


@dataclass(frozen=True)
class SessionHandle:
    session_id: str
    directory: Path
    state_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class RecoveryCandidate:
    session_id: str
    status: str
    updated_at: str


@dataclass(frozen=True)
class RecoveryPrompt:
    requires_user_choice: bool
    sessions: Tuple[RecoveryCandidate, ...]


class SessionRepository:
    """Persist sessions below a stable project-local data directory."""

    def __init__(self, project_directory: Path):
        self.data_root = (Path(project_directory) / DATA_DIRECTORY_NAME).resolve()

    def create_session(self, document_fingerprint: str) -> SessionHandle:
        if not document_fingerprint.strip():
            raise ValueError("document_fingerprint must not be empty")

        session_id = str(uuid.uuid4())
        handle = self._session_handle(document_fingerprint, session_id)
        handle.directory.mkdir(parents=True, exist_ok=False)
        created_at = _utc_now()
        _write_json_atomic(
            handle.state_path,
            {
                "document_fingerprint": document_fingerprint,
                "format_version": STATE_FORMAT_VERSION,
                "model_operation_pending": False,
                "session_id": session_id,
                "session_state": {},
                "status": "idle",
                "updated_at": created_at,
            },
        )
        handle.markdown_path.write_text(
            "# AI Area Assistant session\n\n"
            f"- Session: `{session_id}`\n"
            f"- Created: `{created_at}`\n"
            "- Status: `idle`\n",
            encoding="utf-8",
        )
        return handle

    def recovery_prompt(self, document_fingerprint: str) -> RecoveryPrompt:
        document_key = self._document_key(document_fingerprint)
        sessions_directory = self.data_root / "documents" / document_key / "sessions"
        candidates = []
        if sessions_directory.is_dir():
            for state_path in sessions_directory.glob("*/state.json"):
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    session_id = str(uuid.UUID(state["session_id"]))
                    if (
                        state.get("document_fingerprint") != document_fingerprint
                        or session_id != state["session_id"]
                        or state_path.parent.name != session_id
                        or not isinstance(state["status"], str)
                        or not isinstance(state["updated_at"], str)
                    ):
                        continue
                    candidates.append(
                        RecoveryCandidate(
                            session_id=session_id,
                            status=state["status"],
                            updated_at=state["updated_at"],
                        )
                    )
                except (KeyError, OSError, TypeError, ValueError):
                    # One interrupted or manually damaged session must not hide
                    # other recoverable sessions for the same document.
                    continue
        candidates.sort(key=lambda candidate: candidate.updated_at, reverse=True)
        return RecoveryPrompt(
            requires_user_choice=bool(candidates),
            sessions=tuple(candidates),
        )

    def resume_session(
        self, document_fingerprint: str, session_id: str
    ) -> SessionHandle:
        handle, state = self._load_session(document_fingerprint, session_id)

        resumed_at = _utc_now()
        state.update(
            {
                "model_operation_pending": False,
                "status": "awaiting_user_action",
                "updated_at": resumed_at,
            }
        )
        _write_json_atomic(handle.state_path, state)
        with handle.markdown_path.open("a", encoding="utf-8") as markdown:
            markdown.write(
                f"\n## Resumed `{resumed_at}`\n\n"
                "No model action was replayed; waiting for a new user action.\n"
            )
        return handle

    def record_message(
        self,
        document_fingerprint: str,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("unsupported conversation role")
        handle, state = self._load_session(document_fingerprint, session_id)
        recorded_at = _utc_now()
        redacted_content = _redact_text(content)
        _append_jsonl(
            handle.directory / "conversation.jsonl",
            {"content": redacted_content, "recorded_at": recorded_at, "role": role},
        )
        with handle.markdown_path.open("a", encoding="utf-8") as markdown:
            markdown.write(
                f"\n## {role.title()} `{recorded_at}`\n\n{redacted_content}\n"
            )
        state["updated_at"] = recorded_at
        _write_json_atomic(handle.state_path, state)

    def record_tool_event(
        self,
        document_fingerprint: str,
        session_id: str,
        tool_name: str,
        inputs: Dict[str, Any],
        output: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> None:
        handle, state = self._load_session(document_fingerprint, session_id)
        recorded_at = _utc_now()
        event_type = "tool_error" if error is not None else "tool_call"
        event = redact_sensitive(
            {
                "error": error,
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "inputs": inputs,
                "output": output,
                "recorded_at": recorded_at,
                "tool_name": tool_name,
            }
        )
        _append_jsonl(handle.directory / "operations.jsonl", event)
        _append_jsonl(handle.directory / "agent.log.jsonl", event)
        with handle.markdown_path.open("a", encoding="utf-8") as markdown:
            markdown.write(
                f"\n## Tool `{event['tool_name']}` `{recorded_at}`\n\n"
                f"- Result: `{event_type}`\n"
                "- Input:\n\n"
                "```json\n"
                f"{_pretty_json(event['inputs'])}\n"
                "```\n"
            )
            if event["output"] is not None:
                markdown.write(
                    "- Output:\n\n"
                    "```json\n"
                    f"{_pretty_json(event['output'])}\n"
                    "```\n"
                )
            if event["error"] is not None:
                markdown.write(f"- Error: `{event['error']}`\n")
        state["updated_at"] = recorded_at
        _write_json_atomic(handle.state_path, state)

    def save_machine_state(
        self,
        document_fingerprint: str,
        session_id: str,
        machine_state: Dict[str, Any],
    ) -> None:
        handle, state = self._load_session(document_fingerprint, session_id)
        state["session_state"] = redact_sensitive(machine_state)
        state["updated_at"] = _utc_now()
        _write_json_atomic(handle.state_path, state)

    def load_machine_state(
        self, document_fingerprint: str, session_id: str
    ) -> Dict[str, Any]:
        _, state = self._load_session(document_fingerprint, session_id)
        machine_state = state.get("session_state", {})
        if not isinstance(machine_state, dict):
            raise ValueError("stored session_state must be an object")
        return machine_state

    def load_conversation(
        self, document_fingerprint: str, session_id: str
    ) -> list[Dict[str, str]]:
        """Load the redacted conversation through the durable session boundary."""
        handle, _ = self._load_session(document_fingerprint, session_id)
        path = handle.directory / "conversation.jsonl"
        if not path.is_file():
            return []
        messages = []
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            role = record.get("role")
            content = record.get("content")
            if role not in {"user", "assistant", "system"} or not isinstance(content, str):
                raise ValueError("stored conversation is invalid")
            messages.append({"role": role, "content": content})
        return messages

    def session_directory(
        self, document_fingerprint: str, session_id: str
    ) -> Path:
        handle, _ = self._load_session(document_fingerprint, session_id)
        return handle.directory

    def _load_session(
        self, document_fingerprint: str, session_id: str
    ) -> Tuple[SessionHandle, Dict[str, Any]]:
        try:
            parsed_session_id = str(uuid.UUID(session_id))
        except ValueError as error:
            raise ValueError("invalid session_id") from error
        if parsed_session_id != session_id:
            raise ValueError("invalid session_id")
        handle = self._session_handle(document_fingerprint, session_id)
        if not handle.state_path.is_file():
            raise FileNotFoundError("session not found for document fingerprint")
        state = json.loads(handle.state_path.read_text(encoding="utf-8"))
        if state.get("document_fingerprint") != document_fingerprint:
            raise ValueError("session document fingerprint does not match")
        return handle, state

    def _session_handle(
        self, document_fingerprint: str, session_id: str
    ) -> SessionHandle:
        session_directory = (
            self.data_root
            / "documents"
            / self._document_key(document_fingerprint)
            / "sessions"
            / session_id
        )
        return SessionHandle(
            session_id=session_id,
            directory=session_directory,
            state_path=session_directory / "state.json",
            markdown_path=session_directory / "session.md",
        )

    @staticmethod
    def _document_key(document_fingerprint: str) -> str:
        return hashlib.sha256(document_fingerprint.encode("utf-8")).hexdigest()
