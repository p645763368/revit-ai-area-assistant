import json
import io
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from area_assistant_pyrevit.client import (
    AgentClient,
    AgentConnectionError,
    PlanJobTransportError,
    PlanningRequestTimeout,
    ensure_agent_available,
    planning_timeout_from_environment,
)


class _FakeAgentHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/v1/plan-jobs/"):
            self.server.requests.append(
                (self.path, {"query": parse_qs(parsed.query, keep_blank_values=True)})
            )
            if getattr(self.server, "plan_job_error", False):
                body = json.dumps({
                    "contract_version": "1.0",
                    "message_type": "error",
                    "request_id": "plan-job-status",
                    "code": "job_not_found",
                    "message": "Planning job was not found.",
                    "retryable": False,
                }).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            snapshot = self.server.plan_job_snapshots["get"]
            body = json.dumps({
                "contract_version": "1.0",
                "message_type": "response",
                "request_id": "plan-job-status",
                "status": "accepted" if snapshot["state"] in ("queued", "running") else "completed",
                "payload": snapshot,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
        if self.path == "/v1/plan-jobs":
            snapshot = self.server.plan_job_snapshots["submit"]
            body = json.dumps({
                "contract_version": "1.0",
                "message_type": "response",
                "request_id": request["request_id"],
                "status": "accepted",
                "payload": snapshot,
            }).encode("utf-8")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/v1/plan-jobs/job-a/cancel":
            snapshot = self.server.plan_job_snapshots["cancel"]
            body = json.dumps({
                "contract_version": "1.0",
                "message_type": "response",
                "request_id": request["request_id"],
                "status": "cancelled" if snapshot["state"] == "cancelled" else "completed",
                "payload": snapshot,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/v1/plans":
            delay = getattr(self.server, "plan_delay", 0)
            if delay:
                time.sleep(delay)
            if getattr(self.server, "plan_error", None):
                body = json.dumps({
                    "contract_version": "1.0",
                    "message_type": "error",
                    "request_id": request["request_id"],
                    "code": "planning_failed",
                    "message": self.server.plan_error,
                    "retryable": True,
                    "details": {},
                }).encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
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
    def test_planning_timeout_uses_model_budget_plus_transport_margin(self):
        client = AgentClient(
            "http://127.0.0.1:8765",
            timeout_seconds=2,
            planning_timeout_seconds=135,
        )

        class Response:
            def __init__(self, request):
                envelope = json.loads(request.data.decode("utf-8"))
                self.body = json.dumps({
                    "contract_version": "1.0",
                    "message_type": "response",
                    "request_id": envelope["request_id"],
                    "status": "completed",
                    "payload": {
                        "summary": "done",
                        "question": "choose",
                        "options": [
                            {"id": "a", "label": "A", "recommended": True, "rationale": "r", "impact": "i"},
                            {"id": "b", "label": "B", "recommended": False, "rationale": "r", "impact": "i"},
                        ],
                    },
                }).encode("utf-8")

            def read(self):
                return self.body

            def close(self):
                pass

        observed = []

        def open_request(request, timeout):
            observed.append(timeout)
            return Response(request)

        with patch.dict(
            "os.environ", {"AI_AREA_ASSISTANT_TIMEOUT_SECONDS": "120"}
        ), patch("area_assistant_pyrevit.client.urlopen", side_effect=open_request):
            self.assertEqual(planning_timeout_from_environment(), 135.0)
            client.create_plan(
                "C:\\test", "document-a", "context-a", "panel-a", 1,
                "session-a", "scan",
            )

        self.assertEqual(observed, [135])

    def test_true_planning_timeout_is_specific_and_short_requests_stay_short(self):
        client = AgentClient(
            "http://127.0.0.1:8765",
            timeout_seconds=2,
            planning_timeout_seconds=135,
        )
        observed = []

        def timeout_request(request, timeout):
            observed.append(timeout)
            raise socket.timeout("timed out")

        with patch("area_assistant_pyrevit.client.urlopen", side_effect=timeout_request):
            self.assertFalse(client.is_ready())
            with self.assertRaisesRegex(
                PlanningRequestTimeout, "任务可能仍在本地 Agent 中运行"
            ):
                client.create_plan(
                    "C:\\test", "document-a", "context-a", "panel-a", 1,
                    "session-a", "scan",
                )

        self.assertEqual(observed, [2, 135])

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAgentHandler)
        self.server.requests = []
        snapshot = {
            "job_id": "job-a",
            "state": "queued",
            "stage": "accepted",
            "created_at": "2026-08-21T00:00:00+00:00",
            "updated_at": "2026-08-21T00:00:00+00:00",
            "result": None,
            "error": None,
        }
        self.server.plan_job_snapshots = {
            "submit": snapshot,
            "get": dict(snapshot, state="running", stage="reading_model"),
            "cancel": dict(snapshot, state="cancelled", stage="finished"),
        }
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

    def test_panel_client_submits_polls_and_cancels_plan_jobs_with_short_requests(self):
        client = AgentClient(
            "http://127.0.0.1:{}".format(self.server.server_port),
            timeout_seconds=50,
            job_timeout_seconds=2.0,
        )
        identity = {
            "project_directory": "C:\\test",
            "document_fingerprint": "document-a",
            "context_id": "context-a",
            "panel_instance_id": "panel-a",
            "generation": 1,
            "session_id": "session-a",
        }

        with patch(
            "area_assistant_pyrevit.client.urlopen",
            wraps=__import__("area_assistant_pyrevit.client", fromlist=["urlopen"]).urlopen,
        ) as open_request:
            self.assertTrue(client.is_ready())
            submitted = client.submit_plan_job(
                identity["project_directory"],
                identity["document_fingerprint"],
                identity["context_id"],
                identity["panel_instance_id"],
                identity["generation"],
                identity["session_id"],
                "scan",
                retry_terminal=True,
            )
            polled = client.get_plan_job("job-a", identity)
            cancelled = client.cancel_plan_job("job-a", identity)

        self.assertEqual(submitted["state"], "queued")
        self.assertEqual(polled["state"], "running")
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(
            [call.kwargs["timeout"] for call in open_request.call_args_list],
            [50, 2.0, 2.0, 2.0],
        )
        submit_request = self.server.requests[-3][1]
        poll_request = self.server.requests[-2][1]
        cancel_request = self.server.requests[-1][1]
        self.assertEqual(submit_request["action"], "analysis.plan.submit")
        self.assertEqual(
            submit_request["payload"],
            dict(identity, message="scan", retry_terminal=True),
        )
        self.assertEqual(poll_request["query"], {key: [str(value)] for key, value in identity.items()})
        self.assertEqual(cancel_request["action"], "analysis.plan.cancel")
        self.assertEqual(cancel_request["payload"], identity)

    def test_plan_job_timeout_defaults_to_the_general_timeout(self):
        client = AgentClient(
            "http://127.0.0.1:{}".format(self.server.server_port),
            timeout_seconds=3,
        )
        identity = {
            "project_directory": "C:\\test",
            "document_fingerprint": "document-a",
            "context_id": "context-a",
            "panel_instance_id": "panel-a",
            "generation": 1,
            "session_id": "session-a",
        }

        with patch(
            "area_assistant_pyrevit.client.urlopen",
            wraps=__import__("area_assistant_pyrevit.client", fromlist=["urlopen"]).urlopen,
        ) as open_request:
            client.get_plan_job("job-a", identity)

        self.assertEqual(open_request.call_args.kwargs["timeout"], 3)

    def test_panel_client_rejects_invalid_job_snapshots_and_propagates_versioned_errors(self):
        identity = {
            "project_directory": "C:\\test",
            "document_fingerprint": "document-a",
            "context_id": "context-a",
            "panel_instance_id": "panel-a",
            "generation": 1,
            "session_id": "session-a",
        }
        self.server.plan_job_snapshots["get"] = dict(
            self.server.plan_job_snapshots["get"], error={"code": "private", "message": "bad", "retryable": True}
        )

        with self.assertRaisesRegex(AgentConnectionError, "incompatible v1 response"):
            self.client.get_plan_job("job-a", identity)

        self.server.plan_job_error = True
        with self.assertRaisesRegex(AgentConnectionError, "Planning job was not found"):
            self.client.get_plan_job("job-a", identity)

    def test_plan_job_transport_error_hides_private_exception_text_without_retry(self):
        identity = {
            "project_directory": "C:\\test",
            "document_fingerprint": "document-a",
            "context_id": "context-a",
            "panel_instance_id": "panel-a",
            "generation": 1,
            "session_id": "session-a",
        }
        private_marker = "cedar-private-diagnostic-721"
        requests = []

        def fail_poll(request, timeout):
            requests.append((request.full_url, timeout))
            raise OSError(private_marker)

        with patch("area_assistant_pyrevit.client.urlopen", side_effect=fail_poll):
            with self.assertRaises(PlanJobTransportError) as raised:
                self.client.get_plan_job("job-a", identity)

        self.assertEqual(
            str(raised.exception),
            "Planning job transport is unavailable. Check the local Agent connection.",
        )
        self.assertNotIn(private_marker, str(raised.exception))
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0][0].startswith(self.client.base_url + "/v1/plan-jobs/job-a?"))

    def test_submit_http_rejection_is_definitive_not_ambiguous_transport(self):
        error_body = io.BytesIO(json.dumps({
            "contract_version": "1.0",
            "message_type": "error",
            "request_id": "unknown",
            "code": "planning_rejected",
            "message": "Planning request was rejected.",
            "retryable": True,
        }).encode("utf-8"))
        rejection = HTTPError(
            self.client.base_url + "/v1/plan-jobs",
            409,
            "Conflict",
            {},
            error_body,
        )

        with patch("area_assistant_pyrevit.client.urlopen", side_effect=rejection):
            with self.assertRaises(AgentConnectionError) as raised:
                self.client.submit_plan_job(
                    "C:\\test", "document-a", "context-a", "panel-a", 1,
                    "session-a", "scan", retry_terminal=False,
                )

        self.assertNotIsInstance(raised.exception, PlanJobTransportError)
        self.assertIn("HTTP Error 409", str(raised.exception))

    def test_submit_incompatible_response_is_definitive_not_transport(self):
        class Response:
            def read(self):
                return b"{not-json"

            def close(self):
                pass

        with patch("area_assistant_pyrevit.client.urlopen", return_value=Response()):
            with self.assertRaises(AgentConnectionError) as raised:
                self.client.submit_plan_job(
                    "C:\\test", "document-a", "context-a", "panel-a", 1,
                    "session-a", "scan", retry_terminal=False,
                )

        self.assertNotIsInstance(raised.exception, PlanJobTransportError)
        self.assertIn("incompatible v1 response", str(raised.exception))

    def test_plan_job_poll_timeout_raises_connection_error_without_hidden_retry(self):
        identity = {
            "project_directory": "C:\\test",
            "document_fingerprint": "document-a",
            "context_id": "context-a",
            "panel_instance_id": "panel-a",
            "generation": 1,
            "session_id": "session-a",
        }

        class Response:
            def read(self):
                return json.dumps({
                    "contract_version": "1.0",
                    "message_type": "response",
                    "request_id": "plan-job-status",
                    "status": "accepted",
                    "payload": self_server.plan_job_snapshots["get"],
                }).encode("utf-8")

            def close(self):
                pass

        self_server = self.server
        requests = []

        def poll_request(request, timeout):
            requests.append((request.full_url, timeout))
            if len(requests) == 1:
                raise socket.timeout("timed out")
            return Response()

        with patch("area_assistant_pyrevit.client.urlopen", side_effect=poll_request):
            with self.assertRaises(AgentConnectionError) as raised:
                self.client.get_plan_job("job-a", identity)
            snapshot = self.client.get_plan_job("job-a", identity)

        self.assertNotIsInstance(raised.exception, PlanningRequestTimeout)
        self.assertEqual(snapshot["state"], "running")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(self.server.requests, [])

    def test_plan_can_exceed_short_timeout_but_finish_within_planning_timeout(self):
        self.server.plan_delay = 0.08
        client = AgentClient(
            "http://127.0.0.1:{}".format(self.server.server_port),
            timeout_seconds=0.03,
            planning_timeout_seconds=0.2,
        )

        result = client.create_plan(
            "C:\\test", "document-a", "context-a", "panel-a", 1,
            "session-a", "扫描当前模型",
        )

        self.assertEqual(result["summary"], "已扫描")

    def test_panel_client_surfaces_specific_planning_capability_error(self):
        self.server.plan_error = (
            "Visual evidence is unavailable and no structured boundary geometry was collected."
        )

        with self.assertRaisesRegex(AgentConnectionError, "Visual evidence is unavailable"):
            self.client.create_plan(
                "C:\\test", "document-a", "context-a", "panel-a", 1,
                "session-a", "扫描当前模型",
            )

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
