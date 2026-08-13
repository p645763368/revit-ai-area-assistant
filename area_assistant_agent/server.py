"""Loopback HTTP API for the local Agent."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading

from . import CONTRACT_VERSION, SERVICE_NAME
from .binding_state_store import BindingStateStore
from .document_status_runtime import resolve_document_status
from .model_api import ModelApiError, OpenAICompatibleClient
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
    return server
