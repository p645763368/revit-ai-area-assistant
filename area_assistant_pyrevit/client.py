"""Loopback client used by the pyRevit panel without Revit API dependencies."""

import json
import time
import uuid

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # IronPython 2.7 in pyRevit
    from urllib2 import HTTPError, Request, URLError, urlopen

from area_assistant_agent import CONTRACT_VERSION, SERVICE_NAME


class AgentConnectionError(Exception):
    pass


class AgentClient:
    def __init__(self, base_url="http://127.0.0.1:8765", timeout_seconds=2):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

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
                return json.loads(response.read().decode("utf-8"))
            finally:
                response.close()
        except (HTTPError, OSError, ValueError, URLError) as exc:
            raise AgentConnectionError("Document verification failed: {}".format(exc))


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
