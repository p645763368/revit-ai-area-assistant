"""Loopback client used by the pyRevit panel without Revit API dependencies."""

import json
import os
import re
import socket
import time
import uuid

try:
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
except ImportError:  # IronPython 2.7 in pyRevit
    from urllib import urlencode
    from urllib2 import HTTPError, Request, URLError, urlopen

from area_assistant_agent import CONTRACT_VERSION, SERVICE_NAME

try:
    STRING_TYPES = (basestring,)  # type: ignore[name-defined]
except NameError:
    STRING_TYPES = (str,)

try:
    INTEGER_TYPES = (int, long)  # type: ignore[name-defined]
except NameError:
    INTEGER_TYPES = (int,)


class AgentConnectionError(Exception):
    pass


class PlanJobTransportError(AgentConnectionError):
    """Planning-job outcome is unknown because loopback transport was lost."""


class PlanningRequestTimeout(AgentConnectionError):
    pass


def planning_timeout_from_environment(margin_seconds=15.0):
    try:
        model_timeout = float(
            os.environ.get("AI_AREA_ASSISTANT_TIMEOUT_SECONDS", "30")
        )
    except (TypeError, ValueError):
        model_timeout = 30.0
    if model_timeout <= 0:
        model_timeout = 30.0
    return model_timeout + margin_seconds


def _nonempty_string(value):
    return isinstance(value, STRING_TYPES) and bool(value)


_PLAN_JOB_STATES = {
    "queued", "running", "completed", "failed", "cancelled", "interrupted"
}
_PLAN_JOB_STAGES = {
    "accepted",
    "validating_context",
    "reading_model",
    "capturing_evidence",
    "requesting_model",
    "validating_result",
    "persisting_result",
    "finished",
}
_PLAN_JOB_IDENTITY_FIELDS = {
    "project_directory",
    "document_fingerprint",
    "context_id",
    "panel_instance_id",
    "generation",
    "session_id",
}
_ISO_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_DIAGNOSTIC_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _valid_iso_timestamp(value):
    if not _nonempty_string(value) or not _ISO_TIMESTAMP.match(value):
        return False
    try:
        time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return False
    return True


def _valid_diagnostic_id(value):
    if not _nonempty_string(value):
        return False
    match = _DIAGNOSTIC_ID.match(value)
    return match is not None and len(match.group(0)) == len(value)


def _valid_plan_job_identity(identity):
    return (
        isinstance(identity, dict)
        and set(identity) == _PLAN_JOB_IDENTITY_FIELDS
        and all(
            _nonempty_string(identity.get(key))
            for key in (
                "project_directory",
                "document_fingerprint",
                "context_id",
                "panel_instance_id",
                "session_id",
            )
        )
        and isinstance(identity.get("generation"), INTEGER_TYPES)
        and not isinstance(identity.get("generation"), bool)
        and identity["generation"] >= 0
    )


def _valid_plan_job_snapshot(payload):
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "job_id",
            "state",
            "stage",
            "created_at",
            "updated_at",
            "result",
            "error",
        }
        or not _nonempty_string(payload.get("job_id"))
        or payload.get("state") not in _PLAN_JOB_STATES
        or payload.get("stage") not in _PLAN_JOB_STAGES
        or not _valid_iso_timestamp(payload.get("created_at"))
        or not _valid_iso_timestamp(payload.get("updated_at"))
    ):
        return False
    state = payload["state"]
    if state == "completed":
        return payload.get("error") is None and _valid_session_payload(
            "analysis.plan", payload.get("result")
        )
    if state in ("failed", "interrupted"):
        error = payload.get("error")
        required_error_fields = {"code", "message", "retryable"}
        return (
            payload.get("result") is None
            and isinstance(error, dict)
            and required_error_fields.issubset(error)
            and set(error).issubset(required_error_fields | {"diagnostic_id"})
            and _nonempty_string(error.get("code"))
            and _nonempty_string(error.get("message"))
            and bool(error["code"].strip())
            and bool(error["message"].strip())
            and isinstance(error.get("retryable"), bool)
            and (
                "diagnostic_id" not in error
                or _valid_diagnostic_id(error.get("diagnostic_id"))
            )
        )
    return payload.get("result") is None and payload.get("error") is None


def _valid_session_payload(action, payload):
    if not isinstance(payload, dict):
        return False
    if action == "session.open":
        sessions = payload.get("sessions")
        if (
            set(payload)
            != {
                "active_session_id",
                "context_id",
                "data_root",
                "requires_user_choice",
                "sessions",
            }
            or payload.get("active_session_id") is not None
            or not _nonempty_string(payload.get("context_id"))
            or not _nonempty_string(payload.get("data_root"))
            or payload.get("requires_user_choice") is not True
            or not isinstance(sessions, list)
        ):
            return False
        for item in sessions:
            if (
                not isinstance(item, dict)
                or set(item) != {"session_id", "status", "updated_at"}
                or not _nonempty_string(item.get("session_id"))
                or item.get("status") not in ("idle", "awaiting_user_action")
                or not _nonempty_string(item.get("updated_at"))
            ):
                return False
        return True
    if action == "session.choose":
        return (
            set(payload)
            == {"active_session_id", "context_id", "data_root", "status"}
            and _nonempty_string(payload.get("active_session_id"))
            and _nonempty_string(payload.get("context_id"))
            and _nonempty_string(payload.get("data_root"))
            and payload.get("status") in ("idle", "awaiting_user_action")
        )
    if action == "session.message":
        return (
            set(payload) == {"recorded", "session_id"}
            and payload.get("recorded") is True
            and _nonempty_string(payload.get("session_id"))
        )
    if action == "session.revoke":
        return set(payload) == {"revoked"} and payload.get("revoked") is True
    if action == "analysis.plan":
        if (
            set(payload) != {"summary", "question", "options"}
            or not _nonempty_string(payload.get("summary"))
            or not _nonempty_string(payload.get("question"))
            or not isinstance(payload.get("options"), list)
            or not 2 <= len(payload["options"]) <= 4
        ):
            return False
        recommended = 0
        for option in payload["options"]:
            if (
                not isinstance(option, dict)
                or set(option)
                != {"id", "label", "recommended", "rationale", "impact"}
                or not all(
                    _nonempty_string(option.get(key))
                    for key in ("id", "label", "rationale", "impact")
                )
                or not isinstance(option.get("recommended"), bool)
            ):
                return False
            recommended += int(option["recommended"])
        return recommended == 1
    return False


class AgentClient:
    def __init__(
        self,
        base_url="http://127.0.0.1:8765",
        timeout_seconds=2,
        planning_timeout_seconds=None,
        job_timeout_seconds=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.planning_timeout_seconds = (
            timeout_seconds
            if planning_timeout_seconds is None
            else planning_timeout_seconds
        )
        self.job_timeout_seconds = (
            timeout_seconds
            if job_timeout_seconds is None
            else job_timeout_seconds
        )

    def is_ready(self):
        try:
            response = urlopen(self.base_url + "/health", timeout=self.timeout_seconds)
            try:
                payload = json.loads(response.read().decode("utf-8"))
            finally:
                response.close()
            return (
                payload.get("contract_version") == CONTRACT_VERSION
                and payload.get("message_type") == "response"
                and payload.get("payload", {}).get("service") == SERVICE_NAME
                and payload.get("payload", {}).get("status") == "ready"
            )
        except (HTTPError, OSError, TypeError, ValueError, URLError):
            return False

    def stream_chat(self, message, request_id=None):
        request_id = request_id or "chat-{}".format(uuid.uuid4().hex)
        envelope = {
            "contract_version": CONTRACT_VERSION,
            "message_type": "request",
            "request_id": request_id,
            "action": "chat.stream",
            "payload": {"message": message},
        }
        request = Request(
            self.base_url + "/v1/chat",
            data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            response = urlopen(request, timeout=self.timeout_seconds)
            try:
                for raw_line in response:
                    if raw_line.strip():
                        yield json.loads(raw_line.decode("utf-8"))
            finally:
                response.close()
        except (HTTPError, OSError, ValueError, URLError) as exc:
            raise AgentConnectionError("Local Agent connection failed: {}".format(exc))

    def document_status(self, current_document, pause_reason=None, request_id=None):
        request_id = request_id or "document-status-{}".format(uuid.uuid4().hex)
        envelope = {
            "contract_version": CONTRACT_VERSION,
            "message_type": "request",
            "request_id": request_id,
            "action": "revit.document_status",
            "payload": {
                "current_document": {
                    key: current_document[key]
                    for key in (
                        "revit_instance_id",
                        "document_title",
                        "document_path",
                        "document_fingerprint",
                        "active_view",
                        "is_modified",
                    )
                },
                "previous_document": None,
                "previous_pause_reason": pause_reason,
            },
        }
        request = Request(
            self.base_url + "/v1/document-status",
            data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            response = urlopen(request, timeout=self.timeout_seconds)
            try:
                result = json.loads(response.read().decode("utf-8"))
            finally:
                response.close()
            payload = result.get("payload")
            required_payload_keys = {
                "binding_status",
                "revit_instance_id",
                "document_title",
                "document_path",
                "document_fingerprint",
                "active_view",
                "is_modified",
                "rvt_mcp_status",
                "write_allowed",
                "pause_reason",
            }
            valid_pause_reasons = (
                None,
                "not_bound",
                "revit_instance_changed",
                "document_changed",
                "document_observation_failed",
                "rvt_mcp_instance_mismatch",
                "rvt_mcp_document_mismatch",
                "rvt_mcp_view_mismatch",
                "rvt_mcp_modified_mismatch",
            )
            active_view = payload.get("active_view") if isinstance(payload, dict) else None
            if (
                set(result)
                != {"contract_version", "message_type", "request_id", "status", "payload"}
                or
                result.get("contract_version") != CONTRACT_VERSION
                or result.get("message_type") != "response"
                or result.get("request_id") != request_id
                or result.get("status") != "completed"
                or not isinstance(payload, dict)
                or set(payload) != required_payload_keys
                or payload.get("binding_status") not in ("unbound", "bound", "paused")
                or not isinstance(payload.get("revit_instance_id"), STRING_TYPES)
                or not payload.get("revit_instance_id")
                or not isinstance(payload.get("document_title"), STRING_TYPES)
                or not isinstance(payload.get("document_path"), STRING_TYPES)
                or not isinstance(payload.get("document_fingerprint"), STRING_TYPES)
                or not payload.get("document_fingerprint")
                or not isinstance(active_view, dict)
                or set(active_view) != {"id", "name"}
                or not isinstance(active_view.get("id"), STRING_TYPES)
                or not active_view.get("id")
                or not isinstance(active_view.get("name"), STRING_TYPES)
                or not active_view.get("name")
                or not isinstance(payload.get("is_modified"), bool)
                or not isinstance(payload.get("write_allowed"), bool)
                or payload.get("rvt_mcp_status")
                not in ("verified", "mismatch", "unchecked")
                or payload.get("pause_reason") not in valid_pause_reasons
            ):
                raise AgentConnectionError(
                    "Document verification returned an incompatible v1 response."
                )
            return result
        except AgentConnectionError:
            raise
        except (HTTPError, OSError, ValueError, URLError) as exc:
            raise AgentConnectionError("Document verification failed: {}".format(exc))

    def open_session(
        self,
        project_directory,
        document_fingerprint,
        panel_instance_id,
        generation,
    ):
        return self._post_session_json(
            "/v1/sessions/open",
            "session.open",
            {
                "document_fingerprint": document_fingerprint,
                "generation": generation,
                "panel_instance_id": panel_instance_id,
                "project_directory": project_directory,
            },
        )

    def choose_session(
        self,
        project_directory,
        document_fingerprint,
        context_id,
        panel_instance_id,
        generation,
        choice,
        session_id,
    ):
        return self._post_session_json(
            "/v1/sessions/choose",
            "session.choose",
            {
                "choice": choice,
                "context_id": context_id,
                "document_fingerprint": document_fingerprint,
                "generation": generation,
                "panel_instance_id": panel_instance_id,
                "project_directory": project_directory,
                "session_id": session_id,
            },
        )

    def record_message(
        self,
        project_directory,
        document_fingerprint,
        context_id,
        panel_instance_id,
        generation,
        session_id,
        role,
        content,
    ):
        return self._post_session_json(
            "/v1/sessions/messages",
            "session.message",
            {
                "content": content,
                "context_id": context_id,
                "document_fingerprint": document_fingerprint,
                "generation": generation,
                "panel_instance_id": panel_instance_id,
                "project_directory": project_directory,
                "role": role,
                "session_id": session_id,
            },
        )

    def revoke_session(self, panel_instance_id, generation, context_id):
        return self._post_session_json(
            "/v1/sessions/revoke",
            "session.revoke",
            {
                "context_id": context_id,
                "generation": generation,
                "panel_instance_id": panel_instance_id,
            },
        )

    def submit_plan_job(
        self,
        project_directory,
        document_fingerprint,
        context_id,
        panel_instance_id,
        generation,
        session_id,
        message,
        retry_terminal=False,
    ):
        if type(retry_terminal) is not bool:
            raise AgentConnectionError("Planning job request is invalid.")
        return self._post_plan_job(
            "/v1/plan-jobs",
            "analysis.plan.submit",
            {
                "context_id": context_id,
                "document_fingerprint": document_fingerprint,
                "generation": generation,
                "message": message,
                "panel_instance_id": panel_instance_id,
                "project_directory": project_directory,
                "retry_terminal": retry_terminal,
                "session_id": session_id,
            },
            "submit",
        )

    def get_plan_job(self, job_id, identity):
        if not _nonempty_string(job_id) or not _valid_plan_job_identity(identity):
            raise AgentConnectionError("Planning job request is invalid.")
        request = Request(
            self.base_url + "/v1/plan-jobs/{}?{}".format(
                job_id, urlencode(identity)
            )
        )
        return self._read_plan_job_response(request, "plan-job-status", "get")

    def cancel_plan_job(self, job_id, identity):
        if not _nonempty_string(job_id) or not _valid_plan_job_identity(identity):
            raise AgentConnectionError("Planning job request is invalid.")
        return self._post_plan_job(
            "/v1/plan-jobs/{}/cancel".format(job_id),
            "analysis.plan.cancel",
            identity,
            "cancel",
        )

    def create_plan(
        self,
        project_directory,
        document_fingerprint,
        context_id,
        panel_instance_id,
        generation,
        session_id,
        message,
    ):
        return self._post_session_json(
            "/v1/plans",
            "analysis.plan",
            {
                "context_id": context_id,
                "document_fingerprint": document_fingerprint,
                "generation": generation,
                "message": message,
                "panel_instance_id": panel_instance_id,
                "project_directory": project_directory,
                "session_id": session_id,
            },
            timeout_seconds=self.planning_timeout_seconds,
        )

    def _post_plan_job(self, path, action, payload, operation):
        request_id = "plan-job-{}".format(uuid.uuid4().hex)
        envelope = {
            "contract_version": CONTRACT_VERSION,
            "message_type": "request",
            "request_id": request_id,
            "action": action,
            "payload": payload,
        }
        request = Request(
            self.base_url + path,
            data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        return self._read_plan_job_response(request, request_id, operation)

    def _read_plan_job_response(self, request, request_id, operation):
        try:
            response = urlopen(request, timeout=self.job_timeout_seconds)
        except HTTPError as exc:
            raise AgentConnectionError(_agent_http_error_message(exc, request_id))
        except (IOError, OSError, URLError):
            raise PlanJobTransportError(
                "Planning job transport is unavailable. Check the local Agent connection."
            )
        try:
            try:
                raw_response = response.read()
            except (IOError, OSError, URLError):
                raise PlanJobTransportError(
                    "Planning job transport is unavailable. Check the local Agent connection."
                )
        finally:
            try:
                response.close()
            except Exception:
                pass
        try:
            envelope = json.loads(raw_response.decode("utf-8"))
        except (AttributeError, TypeError, UnicodeError, ValueError):
            raise AgentConnectionError(
                "Planning job request returned an incompatible v1 response."
            )
        snapshot = envelope.get("payload") if isinstance(envelope, dict) else None
        if (
            not isinstance(envelope, dict)
            or set(envelope)
            != {
                "contract_version",
                "message_type",
                "request_id",
                "status",
                "payload",
            }
            or envelope.get("contract_version") != CONTRACT_VERSION
            or envelope.get("message_type") != "response"
            or envelope.get("request_id") != request_id
            or not _valid_plan_job_snapshot(snapshot)
            or not self._valid_plan_job_response_status(
                operation, envelope.get("status"), snapshot["state"]
            )
        ):
            raise AgentConnectionError(
                "Planning job request returned an incompatible v1 response."
            )
        return snapshot

    @staticmethod
    def _valid_plan_job_response_status(operation, status, state):
        if operation == "submit":
            return status == "accepted"
        if state in ("queued", "running"):
            return status == "accepted"
        if state == "cancelled":
            return status == "cancelled"
        return status == "completed"

    def _post_session_json(self, path, action, payload, timeout_seconds=None):
        request_id = "session-{}".format(uuid.uuid4().hex)
        envelope = {
            "contract_version": CONTRACT_VERSION,
            "message_type": "request",
            "request_id": request_id,
            "action": action,
            "payload": payload,
        }
        request = Request(
            self.base_url + path,
            data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            response = urlopen(
                request,
                timeout=(self.timeout_seconds if timeout_seconds is None else timeout_seconds),
            )
            try:
                envelope = json.loads(response.read().decode("utf-8"))
            finally:
                response.close()
            if (
                not isinstance(envelope, dict)
                or set(envelope)
                != {
                    "contract_version",
                    "message_type",
                    "request_id",
                    "status",
                    "payload",
                }
                or envelope.get("contract_version") != CONTRACT_VERSION
                or envelope.get("message_type") != "response"
                or envelope.get("request_id") != request_id
                or envelope.get("status") != "completed"
                or not _valid_session_payload(action, envelope.get("payload"))
            ):
                raise AgentConnectionError(
                    "Session request returned an incompatible v1 response."
                )
            return envelope["payload"]
        except AgentConnectionError:
            raise
        except HTTPError as exc:
            message = _agent_http_error_message(exc, request_id)
            raise AgentConnectionError(message)
        except (OSError, TypeError, ValueError, URLError) as exc:
            if action == "analysis.plan" and _is_timeout_error(exc):
                raise PlanningRequestTimeout(
                    "规划请求等待超时；任务可能仍在本地 Agent 中运行。"
                    "为避免重复计费，请勿立即重试同一规划。"
                )
            raise AgentConnectionError(
                "Local Agent session request failed: {}".format(exc)
            )


def _agent_http_error_message(error, request_id):
    try:
        body = json.loads(error.read().decode("utf-8"))
    except Exception:
        return "Local Agent session request failed: {}".format(error)
    required = {
        "contract_version", "message_type", "request_id", "code", "message", "retryable"
    }
    if (
        isinstance(body, dict)
        and required.issubset(body)
        and set(body).issubset(required | {"details"})
        and body.get("contract_version") == CONTRACT_VERSION
        and body.get("message_type") == "error"
        and body.get("request_id") == request_id
        and _nonempty_string(body.get("code"))
        and _nonempty_string(body.get("message"))
        and isinstance(body.get("retryable"), bool)
        and ("details" not in body or isinstance(body.get("details"), dict))
    ):
        return body["message"]
    return "Local Agent session request failed: {}".format(error)


def _is_timeout_error(error):
    reason = getattr(error, "reason", error)
    errno = getattr(reason, "errno", getattr(error, "errno", None))
    text = str(reason).lower()
    return (
        isinstance(reason, socket.timeout)
        or errno in (110, 10060)
        or "timed out" in text
        or "10060" in text
    )


def ensure_agent_available(client, start_agent, attempts=20, delay_seconds=0.25):
    """Connect to the current singleton or start it and wait for readiness."""

    if client.is_ready():
        return True
    start_agent()
    for _ in range(max(0, attempts - 1)):
        if delay_seconds:
            time.sleep(delay_seconds)
        if client.is_ready():
            return True
    return False
