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
        self.server.requests.append((self.path, request))
        if self.path == "/v1/plans":
            payload = {
                "summary": "已扫描",
                "question": "采用哪个来源？",
                "options": [
                    {"id": "floor", "label": "楼板", "recommended": True, "rationale": "完整", "impact": "继续核对"},
                    {"id": "wall", "label": "墙体", "recommended": False, "rationale": "备选", "impact": "检查连接"},
                ],
            }
            body = json.dumps({
                "contract_version": "1.0", "message_type": "response",
                "request_id": request["request_id"], "status": "completed", "payload": payload,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/v1/sessions/"):
            payload_request = request["payload"]
            if self.path.endswith("/open"):
                payload = {
                    "active_session_id": None,
                    "context_id": "context-a",
                    "data_root": "C:\\test\\AI_Area_Assistant_Data",
                    "requires_user_choice": True,
                    "sessions": [
                        {
                            "session_id": "session-a",
                            "status": "idle",
                            "updated_at": "2026-08-13T00:00:00+00:00",
                        }
                    ],
                }
            elif self.path.endswith("/choose"):
                payload = {
                    "active_session_id": "session-a",
                    "context_id": "context-a",
                    "data_root": "C:\\test\\AI_Area_Assistant_Data",
                    "status": "awaiting_user_action",
                }
            elif self.path.endswith("/revoke"):
                payload = {"revoked": True}
            else:
                payload = {"recorded": True, "session_id": "session-a"}
            payload.update(
                getattr(self.server, "session_response_overrides", {}).get(
                    self.path, {}
                )
            )
            body = json.dumps(
                {
                    "contract_version": "1.0",
                    "message_type": "response",
                    "request_id": request["request_id"],
                    "status": "completed",
                    "payload": payload,
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/v1/document-status":
            response_payload = {
                    "contract_version": "1.0",
                    "message_type": "response",
                    "request_id": request["request_id"],
                    "status": "completed",
                    "payload": {
                        "binding_status": "bound",
                        "revit_instance_id": "revit-19880",
                        "document_title": "Development Copy",
                        "document_path": r"D:\test\development-copy.rvt",
                        "document_fingerprint": "sha256:document",
                        "active_view": {"id": "42", "name": "Level 1"},
                        "is_modified": False,
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
        self.server.requests = []
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

    def test_panel_client_manages_and_records_the_current_document_session(self):
        opened = self.client.open_session("C:\\test", "document-a", "panel-a", 1)
        continued = self.client.choose_session(
            "C:\\test",
            "document-a",
            "context-a",
            "panel-a",
            1,
            "continue",
            "session-a",
        )
        recorded = self.client.record_message(
            "C:\\test",
            "document-a",
            "context-a",
            "panel-a",
            1,
            "session-a",
            "user",
            "hello",
        )
        revoked = self.client.revoke_session("panel-a", 2, "context-a")

        self.assertIsNone(opened["active_session_id"])
        self.assertTrue(opened["requires_user_choice"])
        self.assertEqual(continued["status"], "awaiting_user_action")
        self.assertTrue(recorded["recorded"])
        self.assertTrue(revoked["revoked"])
        self.assertEqual(
            [path for path, _ in self.server.requests],
            [
                "/v1/sessions/open",
                "/v1/sessions/choose",
                "/v1/sessions/messages",
                "/v1/sessions/revoke",
            ],
        )
        for _, request in self.server.requests:
            self.assertEqual(request["contract_version"], "1.0")
            self.assertEqual(request["message_type"], "request")

    def test_panel_client_requests_a_structured_plan_in_the_current_session(self):
        result = self.client.create_plan(
            "C:\\test", "document-a", "context-a", "panel-a", 1,
            "session-a", "扫描当前模型",
        )

        self.assertEqual(result["question"], "采用哪个来源？")
        self.assertTrue(result["options"][0]["recommended"])
        request = self.server.requests[-1][1]
        self.assertEqual(request["action"], "analysis.plan")
        self.assertEqual(request["payload"]["session_id"], "session-a")

    def test_panel_client_rejects_malformed_session_action_payloads(self):
        cases = (
            (
                "/v1/sessions/open",
                {"sessions": "not-a-list"},
                lambda: self.client.open_session(
                    "C:\\test", "document-a", "panel-a", 1
                ),
            ),
            (
                "/v1/sessions/choose",
                {"active_session_id": None},
                lambda: self.client.choose_session(
                    "C:\\test",
                    "document-a",
                    "context-a",
                    "panel-a",
                    1,
                    "continue",
                    "session-a",
                ),
            ),
            (
                "/v1/sessions/messages",
                {"recorded": False},
                lambda: self.client.record_message(
                    "C:\\test",
                    "document-a",
                    "context-a",
                    "panel-a",
                    1,
                    "session-a",
                    "user",
                    "hello",
                ),
            ),
            (
                "/v1/sessions/revoke",
                {"revoked": False},
                lambda: self.client.revoke_session("panel-a", 2, "context-a"),
            ),
        )
        for path, override, request in cases:
            with self.subTest(path=path):
                self.server.session_response_overrides = {path: override}
                with self.assertRaisesRegex(
                    Exception, "incompatible v1 response"
                ):
                    request()

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

    def test_panel_client_rejects_document_response_missing_required_evidence(self):
        self.server.document_response_override = {
            "payload": {
                "binding_status": "bound",
                "rvt_mcp_status": "verified",
                "write_allowed": True,
                "pause_reason": None,
            }
        }
        snapshot = {
            "revit_instance_id": "revit-19880",
            "document_title": "Development Copy",
            "document_path": r"D:\test\development-copy.rvt",
            "document_fingerprint": "sha256:document",
            "active_view": {"id": "42", "name": "Level 1"},
            "is_modified": False,
        }

        with self.assertRaisesRegex(Exception, "incompatible v1 response"):
            self.client.document_status(snapshot, request_id="req-document-incomplete")


if __name__ == "__main__":
    unittest.main()
