"""Loopback HTTP API for the local Agent."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse
import uuid

from . import CONTRACT_VERSION, SERVICE_NAME
from .binding_state_store import BindingStateStore
from .document_status_runtime import resolve_document_status
from .model_api import ModelApiError, OpenAICompatibleClient
from .model_diagnostics import persist_model_diagnostic
from .persistence import SessionRepository
from .planning import KnowledgeCatalog, PlanningAgent
from .planning_jobs import PlanningJobRegistry
from .rvt_mcp_gateway import McpStdioClient


class AgentHttpServer(ThreadingHTTPServer):
    daemon_threads = True


class CancelledPlanningJob(RuntimeError):
    """Stop a worker after its durable job fence has been cancelled."""


def _response(request_id, status, payload):
    return {
        "contract_version": CONTRACT_VERSION,
        "message_type": "response",
        "request_id": request_id,
        "status": status,
        "payload": payload,
    }


def _error(request_id, code, message, retryable, diagnostic_id=None):
    envelope = {
        "contract_version": CONTRACT_VERSION,
        "message_type": "error",
        "request_id": request_id,
        "code": code,
        "message": message,
        "retryable": retryable,
        "details": {},
    }
    if diagnostic_id is not None:
        envelope["diagnostic_id"] = diagnostic_id
    return envelope


def _persist_model_error_diagnostic(
    session_directory, correlation_id, error
):
    if error.diagnostic is None or error.diagnostic_id is None:
        return None
    try:
        persist_model_diagnostic(
            session_directory,
            correlation_id,
            error.diagnostic_id,
            error.diagnostic,
        )
    except Exception:
        return None
    return error.diagnostic_id


def _identity(request):
    return {
        field: request[field]
        for field in (
            "panel_instance_id",
            "generation",
            "context_id",
            "document_fingerprint",
            "session_id",
        )
    }


def _require_current_session_context(server, request, repository, active_session_id):
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
    if server.session_context != expected:
        raise ValueError("stale session context")


def _require_verified_binding(server, document_fingerprint):
    binding = server.current_document_status
    if (
        not isinstance(binding, dict)
        or binding.get("binding_status") != "bound"
        or binding.get("rvt_mcp_status") != "verified"
        or binding.get("document_fingerprint") != document_fingerprint
    ):
        raise ValueError("planning requires the current verified Revit document binding")


def _require_active_planning_context_locked(
    server, request, repository, job_guard=None
):
    if job_guard is not None and not job_guard():
        raise CancelledPlanningJob("planning job is no longer active")
    _require_current_session_context(
        server,
        request,
        repository,
        active_session_id=request["session_id"],
    )
    _require_verified_binding(server, request["document_fingerprint"])


def _require_current_job_identity_locked(server, request, repository):
    panel_id = request.get("panel_instance_id")
    generation = request.get("generation")
    if (
        not isinstance(panel_id, str)
        or not panel_id
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
    ):
        raise LookupError("planning job not found")
    current_generation = server.panel_generations.get(panel_id)
    if current_generation is not None and generation < current_generation:
        raise LookupError("planning job not found")
    if server.session_context is not None:
        expected = (
            panel_id,
            generation,
            request.get("context_id"),
            str(repository.data_root),
            request.get("document_fingerprint"),
            request.get("session_id"),
        )
        if server.session_context != expected:
            raise LookupError("planning job not found")


def _registry_for(server, session_directory):
    canonical_directory = str(Path(session_directory).resolve())
    with server.planning_registries_lock:
        registry = server.planning_job_registries.get(canonical_directory)
        if registry is None:
            registry = PlanningJobRegistry(
                Path(canonical_directory) / "planning_jobs"
            )
            server.planning_job_registries[canonical_directory] = registry
        return registry


def _execute_plan(server, request, job_guard=None, progress=None, started=None):
    """Execute one lock-owned plan without retaining an HTTP handler."""
    repository = SessionRepository(Path(request["project_directory"]))
    document_fingerprint = request["document_fingerprint"]
    session_id = request["session_id"]
    message = request["message"]
    job_guard = job_guard or (lambda: True)
    progress = progress or (lambda stage: None)
    started = started or (lambda: None)

    def require_planning_context_locked():
        _require_active_planning_context_locked(
            server, request, repository, job_guard=job_guard
        )

    with server.planning_lock:
        started()
        with server.session_lock:
            require_planning_context_locked()
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
            with server.session_lock:
                require_planning_context_locked()
                repository.record_tool_event(
                    document_fingerprint,
                    session_id,
                    tool_name=tool_name,
                    inputs=inputs,
                    output=output,
                    error=error,
                )

        def guard_planning_context():
            with server.session_lock:
                require_planning_context_locked()

        screenshot_directory = (session_directory / "screenshots").resolve()

        def commit_screenshot(stable_path, image_bytes):
            candidate = Path(stable_path).resolve()
            if (
                candidate.parent != screenshot_directory
                or candidate.suffix.lower() != ".png"
            ):
                raise ValueError("planning screenshot destination is invalid")
            with server.session_lock:
                require_planning_context_locked()
                screenshot_directory.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(image_bytes)

        result = server.planning_agent.plan(
            conversation,
            session_directory,
            audit,
            document_fingerprint=document_fingerprint,
            session_guard=guard_planning_context,
            screenshot_commit=commit_screenshot,
            progress=progress,
        )
        payload = result.as_dict()
        progress("persisting_result")
        with server.session_lock:
            require_planning_context_locked()
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
        return payload


def _run_plan_job(server, registry, job_id, request, identity):
    try:
        if not registry.is_active(job_id):
            raise CancelledPlanningJob("planning job is no longer active")

        def update_stage(stage):
            if not registry.is_active(job_id):
                raise CancelledPlanningJob("planning job is no longer active")
            registry.transition(job_id, "running", stage)

        def mark_started():
            if not registry.is_active(job_id):
                raise CancelledPlanningJob("planning job is no longer active")
            registry.transition(job_id, "running", "validating_context")

        payload = _execute_plan(
            server,
            request,
            job_guard=lambda: registry.is_active(job_id),
            progress=update_stage,
            started=mark_started,
        )
        repository = SessionRepository(Path(request["project_directory"]))
        with server.session_lock:
            _require_active_planning_context_locked(
                server,
                request,
                repository,
                job_guard=lambda: registry.is_active(job_id),
            )
            registry.transition(job_id, "completed", "finished", result=payload)
    except CancelledPlanningJob:
        registry.cancel(job_id, identity)
    except ModelApiError as error:
        if registry.is_active(job_id):
            public_error = {
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
            }
            diagnostic_id = _persist_model_error_diagnostic(
                registry.storage_root.parent, job_id, error
            )
            if diagnostic_id is not None:
                public_error["diagnostic_id"] = diagnostic_id
            registry.transition(
                job_id,
                "failed",
                "finished",
                error=public_error,
            )
    except Exception:
        if registry.is_active(job_id):
            registry.transition(
                job_id,
                "failed",
                "finished",
                error={
                    "code": "planning_failed",
                    "message": "Planning failed.",
                    "retryable": True,
                },
            )


class AgentRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/v1/plan-jobs/"):
            self._get_plan_job(parsed)
            return
        if parsed.path != "/health" or parsed.query:
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
        if self.path == "/v1/plan-jobs":
            self._submit_plan_job()
            return
        if self.path.startswith("/v1/plan-jobs/") and self.path.endswith("/cancel"):
            self._cancel_plan_job()
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

        diagnostic_target = self._active_session_diagnostic_target()

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
            diagnostic_id = None
            if diagnostic_target is not None:
                diagnostic_id = _persist_model_error_diagnostic(
                    diagnostic_target[0], diagnostic_target[1], exc
                )
            self._write_event(
                _error(
                    request_id,
                    exc.code,
                    str(exc),
                    exc.retryable,
                    diagnostic_id=diagnostic_id,
                )
            )

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
            self._session_repository(request)
            self._document_fingerprint(request)
            payload = _execute_plan(self.server, request)
            self._write_json(200, _response(request_id, "completed", payload))
        except ModelApiError as exc:
            self._write_json(502, _error(request_id, exc.code, str(exc), exc.retryable))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._write_json(
                400 if isinstance(exc, (KeyError, TypeError, ValueError)) else 503,
                _error(request_id, "planning_failed", str(exc), True),
            )

    def _submit_plan_job(self):
        request_id = None
        try:
            request = self._read_session_request(
                "analysis.plan.submit",
                {
                    "context_id",
                    "document_fingerprint",
                    "generation",
                    "message",
                    "panel_instance_id",
                    "project_directory",
                    "retry_terminal",
                    "session_id",
                },
            )
            request_id = request["request_id"]
            message = request["message"]
            retry_terminal = request["retry_terminal"]
            session_id = request["session_id"]
            if (
                not isinstance(message, str)
                or not message.strip()
                or not isinstance(session_id, str)
                or type(retry_terminal) is not bool
            ):
                raise ValueError("invalid planning request")
            repository = self._session_repository(request)
            document_fingerprint = self._document_fingerprint(request)
            self._panel_generation(request)
            identity = _identity(request)
            with self.server.session_lock:
                _require_current_session_context(
                    self.server,
                    request,
                    repository,
                    active_session_id=session_id,
                )
                _require_verified_binding(self.server, document_fingerprint)
                session_directory = repository.session_directory(
                    document_fingerprint, session_id
                )
                registry = _registry_for(self.server, session_directory)
                snapshot, created = registry.submit(
                    identity, message, retry_terminal=retry_terminal
                )
                if created:
                    job_id = snapshot["job_id"]
                    worker = threading.Thread(
                        target=_run_plan_job,
                        args=(self.server, registry, job_id, dict(request), identity),
                        daemon=True,
                        name="planning-job-{}".format(job_id),
                    )
                    self.server.planning_workers[job_id] = worker
                    worker.start()
            self._write_json(
                202 if created else 200,
                _response(request_id, "accepted", snapshot),
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._write_json(
                400 if isinstance(exc, (KeyError, TypeError, ValueError)) else 503,
                _error(request_id, "planning_failed", str(exc), True),
            )

    def _get_plan_job(self, parsed):
        try:
            path_parts = parsed.path.split("/")
            if len(path_parts) != 4 or not path_parts[3]:
                raise LookupError("planning job not found")
            job_id = path_parts[3]
            request = self._read_plan_job_query(parsed.query)
            repository = self._session_repository(request)
            document_fingerprint = self._document_fingerprint(request)
            session_directory = repository.session_directory(
                document_fingerprint, request["session_id"]
            )
            registry = _registry_for(self.server, session_directory)
            with self.server.session_lock:
                _require_current_job_identity_locked(
                    self.server, request, repository
                )
                snapshot = registry.get(job_id, _identity(request))
                if snapshot is not None and snapshot["state"] == "completed":
                    _require_active_planning_context_locked(
                        self.server, request, repository
                    )
            if snapshot is None:
                raise LookupError("planning job not found")
            status = "cancelled" if snapshot["state"] == "cancelled" else "completed"
            if snapshot["state"] in {"queued", "running"}:
                status = "accepted"
            self._write_json(
                200,
                _response("plan-job-status", status, snapshot),
            )
        except (KeyError, LookupError, OSError, TypeError, ValueError, json.JSONDecodeError):
            self._write_json(
                404,
                _error(
                    "plan-job-status",
                    "job_not_found",
                    "Planning job was not found.",
                    False,
                ),
            )

    def _cancel_plan_job(self):
        request_id = None
        try:
            parsed = urlparse(self.path)
            path_parts = parsed.path.split("/")
            if parsed.query or len(path_parts) != 5 or path_parts[4] != "cancel":
                raise ValueError("invalid planning job cancellation path")
            job_id = path_parts[3]
            if not job_id:
                raise ValueError("invalid planning job id")
            request = self._read_session_request(
                "analysis.plan.cancel",
                {
                    "context_id",
                    "document_fingerprint",
                    "generation",
                    "panel_instance_id",
                    "project_directory",
                    "session_id",
                },
            )
            request_id = request["request_id"]
            repository = self._session_repository(request)
            document_fingerprint = self._document_fingerprint(request)
            session_directory = repository.session_directory(
                document_fingerprint, request["session_id"]
            )
            registry = _registry_for(self.server, session_directory)
            with self.server.session_lock:
                _require_current_job_identity_locked(
                    self.server, request, repository
                )
                snapshot = registry.cancel(job_id, _identity(request))
            if snapshot is None:
                raise LookupError("planning job not found")
            status = "cancelled" if snapshot["state"] == "cancelled" else "completed"
            self._write_json(200, _response(request_id, status, snapshot))
        except LookupError:
            self._write_json(
                404,
                _error(
                    request_id,
                    "job_not_found",
                    "Planning job was not found.",
                    False,
                ),
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            self._write_json(
                400,
                _error(request_id, "invalid_request", "Request is invalid.", False),
            )

    @staticmethod
    def _read_plan_job_query(query):
        values = parse_qs(query, keep_blank_values=True)
        required = {
            "project_directory",
            "panel_instance_id",
            "generation",
            "context_id",
            "document_fingerprint",
            "session_id",
        }
        if set(values) != required or any(len(items) != 1 for items in values.values()):
            raise ValueError("invalid planning job query")
        request = {key: items[0] for key, items in values.items()}
        try:
            generation = int(request["generation"])
        except ValueError as error:
            raise ValueError("invalid panel generation") from error
        if generation < 0 or str(generation) != request["generation"]:
            raise ValueError("invalid panel generation")
        request["generation"] = generation
        return request

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

    def _active_session_diagnostic_target(self):
        try:
            with self.server.session_lock:
                context = self.server.session_context
                if context is None or context[5] is None:
                    return
                context_id = context[2]
                data_root = Path(context[3]).resolve()
                document_fingerprint = context[4]
                session_id = context[5]
                repository = SessionRepository(data_root.parent)
                if repository.data_root != data_root:
                    return
                session_directory = repository.session_directory(
                    document_fingerprint, session_id
                )
            return session_directory, context_id
        except Exception:
            return None

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
        _require_current_session_context(
            self.server, request, repository, active_session_id
        )

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
                or not isinstance(payload.get("allow_document_rebind", False), bool)
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
                        allow_document_rebind=payload.get("allow_document_rebind", False),
                    )
                with self.server.session_lock:
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
    server.planning_registries_lock = threading.Lock()
    server.planning_job_registries = {}
    server.planning_workers = {}
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
