"""Loopback client used by the pyRevit panel without Revit API dependencies."""

import json
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CONTRACT_VERSION = "1.0"
SERVICE_NAME = "revit-ai-area-assistant-agent"


class AgentConnectionError(Exception):
    pass


class AgentClient:
    def __init__(self, base_url="http://127.0.0.1:8765", timeout_seconds=2):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def is_ready(self):
        try:
            with urlopen(self.base_url + "/health", timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
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
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                for raw_line in response:
                    if raw_line.strip():
                        yield json.loads(raw_line.decode("utf-8"))
        except (HTTPError, OSError, ValueError, URLError) as exc:
            raise AgentConnectionError("Local Agent connection failed.") from exc


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
