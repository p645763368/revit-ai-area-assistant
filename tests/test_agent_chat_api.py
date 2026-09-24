import json
import io
from pathlib import Path
import threading
import time
import tempfile
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest

from area_assistant_agent.config import AgentConfig
from area_assistant_agent.server import create_server


class _StreamingModelHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.request_count += 1
        content_length = int(self.headers["Content-Length"])
        self.server.received = json.loads(self.rfile.read(content_length))
        self.server.authorization = self.headers.get("Authorization")
        if "/failure/" in self.path or "/auth/" in self.path:
            self.send_response(401 if "/auth/" in self.path else 503)
            self.end_headers()
            self.wfile.write(b"Authorization: Bearer unit-test")
            return
        if "/timeout/" in self.path:
            time.sleep(0.25)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if "blocked-malformed" in self.path:
            self.server.request_started.set()
            if not self.server.release_response.wait(2):
                return
        if "malformed" in self.path:
            event = {
                "choices": [
                    {"delta": {"content": ["secret-stream-content"]}}
                ],
                "unknown_secret": "secret-provider-value",
            }
            self.wfile.write(
                ("data: " + json.dumps(event) + "\n\n").encode("utf-8")
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        payloads = (
            {"choices": [{"delta": {"content": "你好"}}]},
            {"choices": [{"delta": {"content": "，建筑师"}}]},
        )
        if "/disconnect/" in self.path:
            for payload in payloads:
                self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            return
        for payload in payloads + (
            {"choices": [{"finish_reason": "stop"}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 4,
                    "total_tokens": 9,
                },
            },
        ):
            self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, format, *args):
        pass


class AgentChatApiTests(unittest.TestCase):
    def setUp(self):
        self.model_server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingModelHandler)
        self.model_server.request_count = 0
        self.model_server.request_started = threading.Event()
        self.model_server.release_response = threading.Event()
        self.model_thread = threading.Thread(target=self.model_server.serve_forever)
        self.model_thread.daemon = True
        self.model_thread.start()

        model_url = "http://127.0.0.1:{}/v1".format(self.model_server.server_port)
        config = AgentConfig(
            host="127.0.0.1",
            port=0,
            base_url=model_url,
            api_key="unit-test",
            model="test-model",
            timeout_seconds=2,
        )
        self.agent_server = create_server(config)
        self.agent_thread = threading.Thread(target=self.agent_server.serve_forever)
        self.agent_thread.daemon = True
        self.agent_thread.start()

    def tearDown(self):
        self.agent_server.shutdown()
        self.agent_server.server_close()
        self.model_server.shutdown()
        self.model_server.server_close()

    def test_chat_stream_exposes_incremental_reply_through_versioned_envelopes(self):
        request_payload = {
            "contract_version": "1.0",
            "message_type": "request",
            "request_id": "req-chat-1",
            "action": "chat.stream",
            "payload": {"message": "请介绍一下自己"},
        }
        request = Request(
            "http://127.0.0.1:{}/v1/chat".format(self.agent_server.server_port),
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(request, timeout=3) as response:
            events = [json.loads(line) for line in response if line.strip()]

        self.assertEqual(response.headers.get_content_type(), "application/x-ndjson")
        self.assertEqual([event["message_type"] for event in events], ["response"] * 4)
        self.assertEqual([event["status"] for event in events], ["accepted"] * 3 + ["completed"])
        self.assertEqual(
            [event["payload"].get("delta") for event in events[1:3]],
            ["你好", "，建筑师"],
        )
        self.assertEqual(events[-1]["payload"]["message"], "你好，建筑师")
        self.assertTrue(all(event["contract_version"] == "1.0" for event in events))
        self.assertTrue(all(event["request_id"] == "req-chat-1" for event in events))
        self.assertEqual(self.model_server.authorization, "Bearer unit-test")
        self.assertEqual(self.model_server.received["model"], "test-model")
        self.assertEqual(self.model_server.received["messages"][-1]["content"], "请介绍一下自己")
        self.assertTrue(self.model_server.received["stream"])

    def test_health_reports_connection_without_contacting_the_model(self):
        with urlopen(
            "http://127.0.0.1:{}/health".format(self.agent_server.server_port),
            timeout=1,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(
            payload,
            {
                "contract_version": "1.0",
                "message_type": "response",
                "request_id": "health",
                "status": "completed",
                "payload": {
                    "service": "revit-ai-area-assistant-agent",
                    "status": "ready",
                },
            },
        )

    def test_chat_rejects_payloads_that_do_not_match_the_feature_contract(self):
        invalid_payload = {
            "contract_version": "1.0",
            "message_type": "request",
            "request_id": "req-invalid",
            "action": "chat.stream",
            "payload": {"message": "hello", "unexpected": True},
        }
        request = Request(
            "http://127.0.0.1:{}/v1/chat".format(self.agent_server.server_port),
            data=json.dumps(invalid_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=1)
        error = json.loads(raised.exception.read().decode("utf-8"))

        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(error["code"], "invalid_request")

    def test_model_failure_is_retryable_and_does_not_expose_authorization(self):
        self._restart_agent("failure", timeout_seconds=1)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            events = self._send_chat("req-failure")

        self.assertEqual(events[-1]["message_type"], "error")
        self.assertEqual(events[-1]["code"], "model_http_error")
        self.assertTrue(events[-1]["retryable"])
        evidence = json.dumps(events, ensure_ascii=False) + stderr.getvalue()
        self.assertNotIn("unit-test", evidence)
        self.assertNotIn("Authorization", evidence)

    def test_model_disconnect_after_partial_reply_returns_retryable_error(self):
        self._restart_agent("disconnect", timeout_seconds=1)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            events = self._send_chat("req-disconnect")

        self.assertEqual(
            [event.get("payload", {}).get("delta") for event in events[1:3]],
            ["你好", "，建筑师"],
        )
        self.assertFalse(any(event.get("status") == "completed" for event in events))
        self.assertEqual(events[-1]["message_type"], "error")
        self.assertEqual(events[-1]["code"], "model_protocol_error")
        self.assertTrue(events[-1]["retryable"])
        evidence = json.dumps(events, ensure_ascii=False) + stderr.getvalue()
        self.assertNotIn("unit-test", evidence)
        self.assertNotIn("Authorization", evidence)

    def test_model_timeout_returns_a_retryable_error_promptly(self):
        self._restart_agent("timeout", timeout_seconds=0.05)
        started = time.monotonic()
        events = self._send_chat("req-timeout")

        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(events[-1]["code"], "model_timeout")
        self.assertTrue(events[-1]["retryable"])

    def test_model_authentication_failure_can_be_retried_after_configuration_is_fixed(self):
        self._restart_agent("auth", timeout_seconds=1)

        events = self._send_chat("req-auth")

        self.assertEqual(events[-1]["code"], "model_http_error")
        self.assertTrue(events[-1]["retryable"])

    def test_session_chat_protocol_failure_persists_one_safe_diagnostic_without_retry(self):
        self._restart_agent("malformed", timeout_seconds=1)
        self.model_server.request_count = 0
        with tempfile.TemporaryDirectory() as project_directory:
            opened = self._post_session(
                "/v1/sessions/open",
                "session.open",
                {
                    "document_fingerprint": "document-a",
                    "generation": 1,
                    "panel_instance_id": "panel-a",
                    "project_directory": project_directory,
                },
            )
            chosen = self._post_session(
                "/v1/sessions/choose",
                "session.choose",
                {
                    "choice": "new",
                    "context_id": opened["context_id"],
                    "document_fingerprint": "document-a",
                    "generation": 1,
                    "panel_instance_id": "panel-a",
                    "project_directory": project_directory,
                    "session_id": None,
                },
            )
            identity = {
                "context_id": chosen["context_id"],
                "document_fingerprint": "document-a",
                "generation": 1,
                "panel_instance_id": "panel-a",
                "project_directory": project_directory,
                "session_id": chosen["active_session_id"],
            }
            recorded = self._post_session(
                "/v1/sessions/messages",
                "session.message",
                dict(identity, role="user", content="hello"),
            )

            events = self._send_chat("req-malformed-session")
            session_directory = next(
                Path(project_directory).glob(
                    "AI_Area_Assistant_Data/documents/*/sessions/*"
                )
            )
            diagnostics = list(
                session_directory.glob("model_diagnostics/*.jsonl")
            )

            self.assertTrue(recorded["recorded"])
            self.assertEqual(len(events), 2)
            self.assertEqual(events[-1]["message_type"], "error")
            self.assertEqual(events[-1]["code"], "model_protocol_error")
            self.assertEqual(
                events[-1]["message"],
                "Model API returned an incompatible response.",
            )
            self.assertEqual(self.model_server.request_count, 1)
            self.assertEqual(len(diagnostics), 1)
            persisted = diagnostics[0].read_text(encoding="utf-8")
            record = json.loads(persisted)
            self.assertEqual(events[-1]["diagnostic_id"], record["diagnostic_id"])
            self.assertNotIn("secret", persisted)
            self.assertNotIn("secret", json.dumps(events))

    def test_chat_diagnostic_stays_with_request_origin_session_after_session_switch(self):
        self._restart_agent("blocked-malformed", timeout_seconds=2)
        with tempfile.TemporaryDirectory() as project_directory:
            first = self._open_new_session(project_directory, generation=1)
            results = []
            failures = []

            def send_chat():
                try:
                    results.extend(self._send_chat("req-session-switch"))
                except Exception as error:
                    failures.append(error)

            worker = threading.Thread(target=send_chat, daemon=True)
            worker.start()
            self.assertTrue(self.model_server.request_started.wait(1))
            try:
                second = self._open_new_session(project_directory, generation=2)
            finally:
                self.model_server.release_response.set()
            worker.join(2)

            session_directories = list(
                Path(project_directory).glob(
                    "AI_Area_Assistant_Data/documents/*/sessions/*"
                )
            )
            first_directory = next(
                path for path in session_directories if path.name == first["session_id"]
            )
            second_directory = next(
                path for path in session_directories if path.name == second["session_id"]
            )

            self.assertEqual(failures, [])
            self.assertEqual(results[-1]["code"], "model_protocol_error")
            self.assertEqual(
                len(list(first_directory.glob("model_diagnostics/*.jsonl"))), 1
            )
            self.assertEqual(
                list(second_directory.glob("model_diagnostics/*.jsonl")), []
            )

    def _restart_agent(self, prefix, timeout_seconds):
        self.agent_server.shutdown()
        self.agent_server.server_close()
        model_url = "http://127.0.0.1:{}/{}/v1".format(
            self.model_server.server_port, prefix
        )
        config = AgentConfig(
            host="127.0.0.1",
            port=0,
            base_url=model_url,
            api_key="unit-test",
            model="test-model",
            timeout_seconds=timeout_seconds,
        )
        self.agent_server = create_server(config)
        self.agent_thread = threading.Thread(target=self.agent_server.serve_forever)
        self.agent_thread.daemon = True
        self.agent_thread.start()

    def _send_chat(self, request_id):
        payload = {
            "contract_version": "1.0",
            "message_type": "request",
            "request_id": request_id,
            "action": "chat.stream",
            "payload": {"message": "hello"},
        }
        request = Request(
            "http://127.0.0.1:{}/v1/chat".format(self.agent_server.server_port),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            return [json.loads(line) for line in response if line.strip()]

    def _post_session(self, path, action, payload):
        request_id = "req-" + action
        request = Request(
            "http://127.0.0.1:{}{}".format(self.agent_server.server_port, path),
            data=json.dumps(
                {
                    "contract_version": "1.0",
                    "message_type": "request",
                    "request_id": request_id,
                    "action": action,
                    "payload": payload,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        return envelope["payload"]

    def _open_new_session(self, project_directory, generation):
        opened = self._post_session(
            "/v1/sessions/open",
            "session.open",
            {
                "document_fingerprint": "document-a",
                "generation": generation,
                "panel_instance_id": "panel-a",
                "project_directory": project_directory,
            },
        )
        chosen = self._post_session(
            "/v1/sessions/choose",
            "session.choose",
            {
                "choice": "new",
                "context_id": opened["context_id"],
                "document_fingerprint": "document-a",
                "generation": generation,
                "panel_instance_id": "panel-a",
                "project_directory": project_directory,
                "session_id": None,
            },
        )
        return {
            "context_id": chosen["context_id"],
            "session_id": chosen["active_session_id"],
        }


if __name__ == "__main__":
    unittest.main()
