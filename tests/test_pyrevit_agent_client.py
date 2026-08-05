import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from area_assistant_pyrevit.client import AgentClient, ensure_agent_available


class _FakeAgentHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {
                "contract_version": "1.0",
                "message_type": "response",
                "request_id": "health",
                "status": "completed",
                "payload": {
                    "service": "revit-ai-area-assistant-agent",
                    "status": "ready",
                },
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        self.server.request_payload = request
        if self.path == "/v1/document-status":
            response_payload = {
                    "contract_version": "1.0",
                    "message_type": "response",
                    "request_id": request["request_id"],
                    "status": "completed",
                    "payload": {
                        "binding_status": "bound",
                        "rvt_mcp_status": "verified",
                        "write_allowed": True,
                        "pause_reason": None,
                    },
                }
            response_payload.update(
                getattr(self.server, "document_response_override", {})
            )
            body = json.dumps(response_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        events = [
            {
                "contract_version": "1.0",
                "message_type": "response",
                "request_id": request["request_id"],
                "status": "accepted",
                "payload": {"delta": "第一段"},
            },
            {
                "contract_version": "1.0",
                "message_type": "response",
                "request_id": request["request_id"],
                "status": "completed",
                "payload": {"message": "第一段"},
            },
        ]
        body = "".join(json.dumps(event) + "\n" for event in events).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class PyRevitAgentClientTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAgentHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = AgentClient(
            "http://127.0.0.1:{}".format(self.server.server_port), timeout_seconds=1
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_panel_client_observes_health_and_streamed_reply(self):
        self.assertTrue(self.client.is_ready())

        events = list(self.client.stream_chat("计算这一层", request_id="req-panel-1"))

        self.assertEqual(events[0]["payload"]["delta"], "第一段")
        self.assertEqual(events[-1]["status"], "completed")
        self.assertEqual(self.server.request_payload["action"], "chat.stream")
        self.assertEqual(self.server.request_payload["payload"]["message"], "计算这一层")

    def test_panel_starts_agent_once_then_waits_until_it_is_ready(self):
        class InitiallyStoppedClient:
            def __init__(self):
                self.checks = 0

            def is_ready(self):
                self.checks += 1
                return self.checks >= 3

        stopped_client = InitiallyStoppedClient()
        starts = []

        available = ensure_agent_available(
            stopped_client,
            lambda: starts.append("started"),
            attempts=3,
            delay_seconds=0,
        )

        self.assertTrue(available)
        self.assertEqual(starts, ["started"])
        self.assertEqual(stopped_client.checks, 3)

    def test_panel_client_sends_document_snapshot_through_versioned_contract(self):
        snapshot = {
            "revit_instance_id": "revit-19880",
            "document_title": "Development Copy",
            "document_path": r"D:\test\development-copy.rvt",
            "document_fingerprint": "sha256:document",
            "active_view": {"id": "42", "name": "Level 1"},
            "is_modified": False,
            "authorized_path_match": True,
        }

        response = self.client.document_status(
            snapshot, pause_reason=None, request_id="req-document-1"
        )

        self.assertEqual(response["payload"]["binding_status"], "bound")
        self.assertEqual(self.server.request_payload["action"], "revit.document_status")
        self.assertEqual(
            self.server.request_payload["payload"]["current_document"]["document_path"],
            snapshot["document_path"],
        )
        self.assertNotIn(
            "authorized_path_match",
            self.server.request_payload["payload"]["current_document"],
        )

    def test_panel_client_rejects_unsupported_document_response_version(self):
        self.server.document_response_override = {"contract_version": "2.0"}
        snapshot = {
            "revit_instance_id": "revit-19880",
            "document_title": "Development Copy",
            "document_path": r"D:\test\development-copy.rvt",
            "document_fingerprint": "sha256:document",
            "active_view": {"id": "42", "name": "Level 1"},
            "is_modified": False,
        }

        with self.assertRaisesRegex(Exception, "incompatible v1 response"):
            self.client.document_status(snapshot, request_id="req-document-version")

    def test_panel_client_rejects_document_response_for_another_request(self):
        self.server.document_response_override = {"request_id": "stale-request"}
        snapshot = {
            "revit_instance_id": "revit-19880",
            "document_title": "Development Copy",
            "document_path": r"D:\test\development-copy.rvt",
            "document_fingerprint": "sha256:document",
            "active_view": {"id": "42", "name": "Level 1"},
            "is_modified": False,
        }

        with self.assertRaisesRegex(Exception, "incompatible v1 response"):
            self.client.document_status(snapshot, request_id="req-document-current")


if __name__ == "__main__":
    unittest.main()
