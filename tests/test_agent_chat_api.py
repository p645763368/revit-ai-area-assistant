import json
import io
import threading
import time
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
import unittest

from area_assistant_agent.config import AgentConfig
from area_assistant_agent.server import create_server


class _StreamingModelHandler(BaseHTTPRequestHandler):
    def do_POST(self):
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
        for payload in (
            {"choices": [{"delta": {"content": "你好"}}]},
            {"choices": [{"delta": {"content": "，建筑师"}}]},
        ):
            self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, format, *args):
        pass


class AgentChatApiTests(unittest.TestCase):
    def setUp(self):
        self.model_server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingModelHandler)
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


if __name__ == "__main__":
    unittest.main()
