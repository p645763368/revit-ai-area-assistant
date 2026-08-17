"""Loopback HTTP API for the local Agent."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import uuid

from . import CONTRACT_VERSION, SERVICE_NAME
from .binding_state_store import BindingStateStore
from .document_status_runtime import resolve_document_status
from .model_api import ModelApiError, OpenAICompatibleClient
from .persistence import SessionRepository
from .planning import KnowledgeCatalog, PlanningAgent
from .rvt_mcp_gateway import McpStdioClient


class AgentHttpServer(ThreadingHTTPServer):
    daemon_threads = True


def _response(request_id, status, payload):
    return {
        "contract_version": CONTRACT_VERSION,
        "message_type": "response",
        "request_id": request_id,
        "status": status,
        "payload": payload,
    }


def _error(request_id, code, message, retryable):
    return {
        "contract_version": CONTRACT_VERSION,
        "message_type": "error",
        "request_id": request_id,
        "code": code,
        "message": message,
        "retryable": retryable,
        "details": {},
    }


class AgentRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._write_json(
            200,
            _response(
                "health",
                "completed",
                {"service": SERVICE_NAME, "status": "ready"},
            ),
        )

    def do_POST(self):
        if self.path == "/v1/sessions/open":
            self._open_session()
            return
        if self.path == "/v1/sessions/choose":
            self._choose_session()
            return
        if self.path == "/v1/sessions/messages":
            self._record_session_message()
            return
        if self.path == "/v1/sessions/revoke":
            self._revoke_session()
            return
        if self.path == "/v1/plans":
            self._create_plan()
            return
        if self.path == "/v1/document-status":
            self._handle_document_status()
            return
        if self.path != "/v1/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            request_id = request.get("request_id")
            payload = request.get("payload")
            if (
                set(request) != {"contract_version", "message_type", "request_id", "action", "payload"}
                or request.get("contract_version") != CONTRACT_VERSION
                or request.get("message_type") != "request"
                or request.get("action") != "chat.stream"
                or not request_id
                or not isinstance(payload, dict)
                or set(payload) != {"message"}
                or not isinstance(payload.get("message"), str)
                or not payload["message"].strip()
            ):
                raise ValueError("invalid request")
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
            self._write_json(400, _error(None, "invalid_request", "Request is invalid.", False))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self._write_event(_response(request_id, "accepted", {"event": "started"}))
        complete = []
        try:
            for delta in self.server.model_client.stream_reply(request["payload"]["message"]):
                complete.append(delta)
                self._write_event(_response(request_id, "accepted", {"delta": delta}))
            self._write_event(_response(request_id, "completed", {"message": "".join(complete)}))
        except ModelApiError as exc:
            self._write_event(_error(request_id, exc.code, str(exc), exc.retryable))

    def _open_session(self):
        try:
            request = self._read_session_request(
                "session.open",
                {
                    "document_fingerprint",
                    "generation",
                    "panel_instance_id",
                    "project_directory",
                },
            )
            repository = self._session_repository(request)
            document_fingerprint = self._document_fingerprint(request)
            with self.server.session_lock:
                panel_id, generation = self._panel_generation(request)
                if generation < self.server.panel_generations.get(panel_id, -1):
                    raise ValueError("stale panel generation")
                self.server.panel_generations[panel_id] = generation
                context_id = uuid.uuid4().hex
                self.server.session_context = (
                    panel_id,
                    generation,
                    context_id,
                    str(repository.data_root),
                    document_fingerprint,
                    None,
                )
                prompt = repository.recovery_prompt(document_fingerprint)
            self._write_json(
                200,
                _response(
                    request["request_id"],
                    "completed",
                    {
                        "active_session_id": None,
                        "context_id": context_id,
                        "data_root": str(repository.data_root),
                        "requires_user_choice": True,
                        "sessions": [
                            {
                                "session_id": item.session_id,
                                "status": item.status,
                                "updated_at": item.updated_at,
                            }
                            for item in prompt.sessions
                        ],
                    },
                ),
            )
        except (KeyError, OSError, TypeError, ValueError):
            self._write_json(
                400, _error(None, "invalid_request", "Request is invalid.", False)
            )

    def _choose_session(self):
        try:
            request = self._read_session_request(
                "session.choose",
                {
                    "choice",
                    "context_id",
                    "document_fingerprint",
                    "generation",
                    "panel_instance_id",
                    "project_directory",
                    "session_id",
                }
            )
            repository = self._session_repository(request)
            document_fingerprint = self._document_fingerprint(request)
            choice = request["choice"]
            session_id = request["session_id"]
            if choice not in {"continue", "new"}:
                raise ValueError("invalid choice")
            if choice == "continue" and not isinstance(session_id, str):
                raise ValueError("missing session")
            if choice == "new" and session_id is not None:
                raise ValueError("unexpected session")
            with self.server.session_lock:
                self._require_current_session_context(
                    request, repository, active_session_id=None
                )
                if choice == "continue":
                    handle = repository.resume_session(
                        document_fingerprint, session_id
                    )
                    status = "awaiting_user_action"
                else:
                    handle = repository.create_session(document_fingerprint)
                    status = "idle"
                self.server.session_context = (
                    request["panel_instance_id"],
                    request["generation"],
                    request["context_id"],
                    str(repository.data_root),
                    document_fingerprint,
                    handle.session_id,
                )
            self._write_json(
                200,
                _response(
                    request["request_id"],
                    "completed",
                    {
                        "active_session_id": handle.session_id,
                        "context_id": request["context_id"],
                        "data_root": str(repository.data_root),
                        "status": status,
                    },
                ),
            )
        except (KeyError, OSError, TypeError, ValueError):
            self._write_json(
                400, _error(None, "invalid_request", "Request is invalid.", False)
            )

    def _record_session_message(self):
        try:
            request = self._read_session_request(
                "session.message",
                {
                    "content",
                    "context_id",
                    "document_fingerprint",
                    "generation",
                    "panel_instance_id",
                    "project_directory",
                    "role",
                    "session_id",
                }
            )
            repository = self._session_repository(request)
            document_fingerprint = self._document_fingerprint(request)
            role = request["role"]
            content = request["content"]
            session_id = request["session_id"]
            if (
                role not in {"user", "assistant"}
                or not isinstance(content, str)
                or not isinstance(session_id, str)
            ):
                raise ValueError("invalid message")
            with self.server.session_lock:
                self._require_current_session_context(
                    request, repository, active_session_id=session_id
                )
                repository.record_message(
                    document_fingerprint,
                    session_id,
                    role=role,
                    content=content,
                )
            self._write_json(
                200,
                _response(
                    request["request_id"],
                    "completed",
                    {"recorded": True, "session_id": session_id},
                ),
            )
        except (KeyError, OSError, TypeError, ValueError):
            self._write_json(
                400, _error(None, "invalid_request", "Request is invalid.", False)
            )

    def _revoke_session(self):
        try:
            request = self._read_session_request(
                "session.revoke",
                {"context_id", "generation", "panel_instance_id"},
            )
            panel_id, generation = self._panel_generation(request)
            with self.server.session_lock:
                current_generation = self.server.panel_generations.get(panel_id, -1)
                if generation >= current_generation:
                    self.server.panel_generations[panel_id] = generation
                    if (
                        self.server.session_context is not None
                        and self.server.session_context[0] == panel_id
                        and self.server.session_context[1] < generation
                    ):
                        self.server.session_context = None
            self._write_json(
                200,
                _response(
                    request["request_id"],
                    "completed",
                    {"revoked": True},
                ),
            )
        except (KeyError, OSError, TypeError, ValueError):
            self._write_json(
                400, _error(None, "invalid_request", "Request is invalid.", False)
            )

    def _create_plan(self):
        request_id = None
        try:
            request = self._read_session_request(
                "analysis.plan",
                {
                    "context_id",
                    "document_fingerprint",
                    "generation",
                    "message",
                    "panel_instance_id",
                    "project_directory",
                    "session_id",
                },
            )
            request_id = request["request_id"]
            message = request["message"]
            session_id = request["session_id"]
            if not isinstance(message, str) or not message.strip() or not isinstance(session_id, str):
                raise ValueError("invalid planning request")
            repository = self._session_repository(request)
            document_fingerprint = self._document_fingerprint(request)
            with self.server.planning_lock:
                with self.server.session_lock:
                    self._require_current_session_context(
                        request, repository, active_session_id=session_id
                    )
                    binding = self.server.current_document_status
                    if (
                        not isinstance(binding, dict)
                        or binding.get("binding_status") != "bound"
                        or binding.get("rvt_mcp_status") != "verified"
                        or binding.get("document_fingerprint") != document_fingerprint
                    ):
                        raise ValueError(
                            "planning requires the current verified Revit document binding"
                        )
                    repository.record_message(
                        document_fingerprint, session_id, role="user", content=message
                    )
                    conversation = repository.load_conversation(
                        document_fingerprint, session_id
                    )
                    session_directory = repository.session_directory(
                        document_fingerprint, session_id
                    )

                def audit(tool_name, inputs, output, error):
                    with self.server.session_lock:
                        self._require_current_session_context(
                            request, repository, active_session_id=session_id
                        )
                        repository.record_tool_event(
                            document_fingerprint,
                            session_id,
                            tool_name=tool_name,
                            inputs=inputs,
                            output=output,
                            error=error,
                        )

                def guard_planning_context():
                    with self.server.session_lock:
                        self._require_current_session_context(
                            request, repository, active_session_id=session_id
                        )
                        binding = self.server.current_document_status
                        if (
                            not isinstance(binding, dict)
                            or binding.get("binding_status") != "bound"
                            or binding.get("rvt_mcp_status") != "verified"
                            or binding.get("document_fingerprint") != document_fingerprint
                        ):
                            raise ValueError("planning document context is no longer current")

                result = self.server.planning_agent.plan(
                    conversation,
                    session_directory,
                    audit,
                    document_fingerprint=document_fingerprint,
                    session_guard=guard_planning_context,
                )
                payload = result.as_dict()
                with self.server.session_lock:
                    self._require_current_session_context(
                        request, repository, active_session_id=session_id
                    )
                    repository.record_message(
                        document_fingerprint,
                        session_id,
                        role="assistant",
                        content=json.dumps(payload, ensure_ascii=False),
                    )
                    machine_state = repository.load_machine_state(
                        document_fingerprint, session_id
                    )
                    machine_state["last_plan"] = payload
                    repository.save_machine_state(
                        document_fingerprint, session_id, machine_state
                    )
            self._write_json(200, _response(request_id, "completed", payload))
        except ModelApiError as exc:
            self._write_json(502, _error(request_id, exc.code, str(exc), exc.retryable))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._write_json(
                400 if isinstance(exc, (KeyError, TypeError, ValueError)) else 503,
                _error(request_id, "planning_failed", str(exc), True),
            )

    def _read_session_request(self, action, required_payload_keys):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        if (
            not isinstance(request, dict)
            or set(request)
            != {"contract_version", "message_type", "request_id", "action", "payload"}
            or request.get("contract_version") != CONTRACT_VERSION
            or request.get("message_type") != "request"
            or not isinstance(request.get("request_id"), str)
            or not request["request_id"]
            or request.get("action") != action
            or not isinstance(request.get("payload"), dict)
            or set(request["payload"]) != required_payload_keys
        ):
            raise ValueError("invalid request")
        payload = request["payload"]
        payload["request_id"] = request["request_id"]
        return payload

    @staticmethod
    def _panel_generation(request):
        panel_id = request.get("panel_instance_id")
        generation = request.get("generation")
        if (
            not isinstance(panel_id, str)
            or not panel_id
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            raise ValueError("invalid panel generation")
        return panel_id, generation

    @staticmethod
    def _session_repository(request):
        project_directory = request["project_directory"]
        if not isinstance(project_directory, str):
            raise ValueError("invalid project directory")
        path = Path(project_directory)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("invalid project directory")
        return SessionRepository(path)

    @staticmethod
    def _document_fingerprint(request):
        document_fingerprint = request["document_fingerprint"]
        if (
            not isinstance(document_fingerprint, str)
            or not document_fingerprint.strip()
        ):
            raise ValueError("invalid document fingerprint")
        return document_fingerprint

    def _require_current_session_context(
        self, request, repository, active_session_id
    ):
        context_id = request.get("context_id")
        if not isinstance(context_id, str) or not context_id:
            raise ValueError("invalid session context")
        expected = (
            request["panel_instance_id"],
            request["generation"],
            context_id,
            str(repository.data_root),
            request["document_fingerprint"],
            active_session_id,
        )
        if self.server.session_context != expected:
            raise ValueError("stale session context")

    def _handle_document_status(self):
        request_id = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            request_id = request.get("request_id")
            payload = request.get("payload")
            if (
                request.get("contract_version") != CONTRACT_VERSION
                or request.get("message_type") != "request"
                or request.get("action") != "revit.document_status"
                or not request_id
                or not isinstance(payload, dict)
            ):
                raise ValueError("invalid request")
            with self.server.document_status_lock:
                with McpStdioClient() as client:
                    response = resolve_document_status(
                        request_id=request_id,
                        current_payload=payload["current_document"],
                        previous_payload=payload.get("previous_document"),
                        previous_pause_reason=payload.get("previous_pause_reason"),
                        authorized_document_path=os.environ.get(
                            "AI_AREA_ASSISTANT_TEST_DOCUMENT", ""
                        ),
                        client=client,
                        binding_store=self.server.binding_store,
                    )
                self.server.current_document_status = response["payload"]
            self._write_json(200, response)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            self._write_json(
                503,
                _error(
                    request_id,
                    "document_status_unavailable",
                    str(exc),
                    True,
                ),
            )

    def _write_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_event(self, payload):
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(line)
        self.wfile.flush()

    def log_message(self, format, *args):
        # Avoid accidental request/header logging. Operational logging is added
        # at explicit call sites with redacted, structured fields only.
        return


def create_server(config):
    server = AgentHttpServer((config.host, config.port), AgentRequestHandler)
    server.model_client = OpenAICompatibleClient(config)
    server.binding_store = BindingStateStore()
    server.document_status_lock = threading.Lock()
    server.session_lock = threading.Lock()
    server.planning_lock = threading.Lock()
    server.session_context = None
    server.panel_generations = {}
    server.current_document_status = None
    knowledge_root = Path(__file__).resolve().parents[1] / "knowledge"
    server.planning_agent = PlanningAgent(
        server.model_client,
        KnowledgeCatalog(knowledge_root),
        McpStdioClient,
    )
    return server
