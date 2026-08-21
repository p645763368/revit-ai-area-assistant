import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import tempfile
import unittest

from area_assistant_agent.config import AgentConfig
from area_assistant_agent.planning import PlanningResult
from area_assistant_agent.planning_jobs import PlanningJobRegistry
from area_assistant_agent.server import create_server


class _BlockingPlanner:
    def __init__(self, started, release):
        self.started = started
        self.release = release
        self.calls = 0

    def plan(self, conversation, session_directory, audit, **kwargs):
        self.calls += 1
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError("test did not release planner")
        return PlanningResult.from_dict(
            {
                "summary": "Read-only scan completed.",
                "question": "Which source should be compared next?",
                "options": [
                    {
                        "id": "floor",
                        "label": "Floor",
                        "recommended": True,
                        "rationale": "Exact boundary is available.",
                        "impact": "Continue with the floor boundary.",
                    },
                    {
                        "id": "wall",
                        "label": "Walls",
                        "recommended": False,
                        "rationale": "Useful as supporting evidence.",
                        "impact": "Check wall joins before continuing.",
                    },
                ],
            }
        )


class _CommitAfterReleasePlanner(_BlockingPlanner):
    def plan(self, conversation, session_directory, audit, **kwargs):
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError("test did not release planner")
        stable_path = Path(session_directory) / "screenshots" / "late.png"
        kwargs["screenshot_commit"](stable_path, b"late screenshot")
        audit(
            "capture_revit_view",
            {},
            {"saved_path": str(stable_path)},
            None,
        )
        return super().plan(conversation, session_directory, audit, **kwargs)


class AgentPlanningJobsApiTests(unittest.TestCase):
    def setUp(self):
        config = AgentConfig(
            "127.0.0.1",
            0,
            "http://127.0.0.1:1/v1",
            "unit-test",
            "test-model",
            1,
        )
        self.server = create_server(config)
        self.server.current_document_status = {
            "binding_status": "bound",
            "rvt_mcp_status": "verified",
            "document_fingerprint": "document-a",
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.project = tempfile.TemporaryDirectory()
        opened = self._post(
            "/v1/sessions/open",
            "session.open",
            {
                "document_fingerprint": "document-a",
                "generation": 1,
                "panel_instance_id": "panel-a",
                "project_directory": self.project.name,
            },
        )[1]
        chosen = self._post(
            "/v1/sessions/choose",
            "session.choose",
            {
                "choice": "new",
                "context_id": opened["context_id"],
                "document_fingerprint": "document-a",
                "generation": 1,
                "panel_instance_id": "panel-a",
                "project_directory": self.project.name,
                "session_id": None,
            },
        )[1]
        self.identity = {
            "context_id": chosen["context_id"],
            "document_fingerprint": "document-a",
            "generation": 1,
            "panel_instance_id": "panel-a",
            "project_directory": self.project.name,
            "session_id": chosen["active_session_id"],
        }

    def tearDown(self):
        for worker in getattr(self.server, "planning_workers", {}).values():
            worker.join(2)
        self.server.shutdown()
        self.server.server_close()
        self.project.cleanup()

    def test_submit_returns_before_blocking_planner_and_poll_completes_after_release(self):
        started = threading.Event()
        release = threading.Event()
        self.server.planning_agent = _BlockingPlanner(started, release)

        submitted = self._submit_plan_job("scan")

        self.assertTrue(started.wait(1))
        self.assertIn(submitted["state"], {"queued", "running"})
        self.assertFalse(release.is_set())
        release.set()
        self.assertEqual(
            self._wait_for_terminal(submitted["job_id"])["state"], "completed"
        )

    def test_cancel_is_idempotent_and_blocks_late_durable_commits(self):
        started = threading.Event()
        release = threading.Event()
        self.server.planning_agent = _CommitAfterReleasePlanner(started, release)
        submitted = self._submit_plan_job("scan then wait")
        self.assertTrue(started.wait(1))
        session_directory = next(
            Path(self.project.name).glob(
                "AI_Area_Assistant_Data/documents/*/sessions/*"
            )
        )
        conversation_path = session_directory / "conversation.jsonl"
        conversation_before = conversation_path.read_bytes()

        first = self._cancel_plan_job(submitted["job_id"])
        second = self._cancel_plan_job(submitted["job_id"])
        release.set()
        self.server.planning_workers[submitted["job_id"]].join(2)

        self.assertEqual(first["state"], "cancelled")
        self.assertEqual(second, first)
        self.assertEqual(
            self._get_plan_job(submitted["job_id"])["state"], "cancelled"
        )
        self.assertEqual(conversation_path.read_bytes(), conversation_before)
        self.assertEqual(list((session_directory / "screenshots").glob("*")), [])
        self.assertFalse((session_directory / "operations.jsonl").exists())
        state = json.loads((session_directory / "state.json").read_text("utf-8"))
        self.assertNotIn("last_plan", state["session_state"])

    def test_duplicate_submission_reuses_job_and_invokes_planner_once(self):
        started = threading.Event()
        release = threading.Event()
        planner = _BlockingPlanner(started, release)
        self.server.planning_agent = planner

        first = self._submit_plan_job("  scan   model ")
        self.assertTrue(started.wait(1))
        second = self._submit_plan_job("scan model", expected_status=200)

        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(planner.calls, 1)
        release.set()
        self.assertEqual(
            self._wait_for_terminal(first["job_id"])["state"], "completed"
        )

    def test_session_revocation_invalidates_running_job_without_late_commits(self):
        started = threading.Event()
        release = threading.Event()
        self.server.planning_agent = _CommitAfterReleasePlanner(started, release)
        submitted = self._submit_plan_job("scan before revoke")
        self.assertTrue(started.wait(1))
        session_directory = next(
            Path(self.project.name).glob(
                "AI_Area_Assistant_Data/documents/*/sessions/*"
            )
        )
        conversation_path = session_directory / "conversation.jsonl"
        conversation_before = conversation_path.read_bytes()

        self._post(
            "/v1/sessions/revoke",
            "session.revoke",
            {
                "context_id": self.identity["context_id"],
                "generation": 2,
                "panel_instance_id": "panel-a",
            },
        )
        release.set()
        self.server.planning_workers[submitted["job_id"]].join(2)
        registry = next(iter(self.server.planning_job_registries.values()))
        terminal = registry.get(
            submitted["job_id"],
            {
                key: self.identity[key]
                for key in (
                    "panel_instance_id",
                    "generation",
                    "context_id",
                    "document_fingerprint",
                    "session_id",
                )
            },
        )

        self.assertEqual(terminal["state"], "failed")
        self.assertEqual(terminal["error"]["code"], "planning_failed")
        with self.assertRaises(HTTPError) as raised:
            self._get_plan_job(submitted["job_id"])
        self.assertEqual(raised.exception.code, 404)
        self.assertEqual(
            json.loads(raised.exception.read().decode("utf-8"))["code"],
            "job_not_found",
        )
        self.assertEqual(conversation_path.read_bytes(), conversation_before)
        self.assertEqual(list((session_directory / "screenshots").glob("*")), [])
        self.assertFalse((session_directory / "operations.jsonl").exists())
        state = json.loads((session_directory / "state.json").read_text("utf-8"))
        self.assertNotIn("last_plan", state["session_state"])

    def test_cross_context_poll_returns_versioned_job_not_found(self):
        started = threading.Event()
        release = threading.Event()
        release.set()
        self.server.planning_agent = _BlockingPlanner(started, release)
        submitted = self._submit_plan_job("scan")
        self.assertTrue(started.wait(1))
        self._wait_for_terminal(submitted["job_id"])
        other_identity = dict(self.identity, context_id="other-context")

        with self.assertRaises(HTTPError) as raised:
            self._get_plan_job(submitted["job_id"], other_identity)

        self.assertEqual(raised.exception.code, 404)
        envelope = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual(envelope["contract_version"], "1.0")
        self.assertEqual(envelope["message_type"], "error")
        self.assertEqual(envelope["code"], "job_not_found")

    def test_poll_reloads_registry_and_marks_unfinished_job_interrupted(self):
        session_directory = next(
            Path(self.project.name).glob(
                "AI_Area_Assistant_Data/documents/*/sessions/*"
            )
        ).resolve()
        registry = PlanningJobRegistry(session_directory / "planning_jobs")
        job, _ = registry.submit(
            {
                key: self.identity[key]
                for key in (
                    "panel_instance_id",
                    "generation",
                    "context_id",
                    "document_fingerprint",
                    "session_id",
                )
            },
            "persisted scan",
        )
        registry.transition(job["job_id"], "running", "reading_model")
        self.server.planning_job_registries.clear()

        recovered = self._get_plan_job(job["job_id"])

        self.assertEqual(recovered["state"], "interrupted")
        self.assertEqual(recovered["error"]["code"], "agent_restarted")
        self.assertEqual(
            list(self.server.planning_job_registries), [str(session_directory)]
        )

    def test_legacy_plans_route_still_completes_synchronously(self):
        started = threading.Event()
        release = threading.Event()
        release.set()
        planner = _BlockingPlanner(started, release)
        self.server.planning_agent = planner

        status, payload = self._post(
            "/v1/plans",
            "analysis.plan",
            dict(self.identity, message="legacy scan"),
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["options"][0]["id"], "floor")
        self.assertEqual(planner.calls, 1)

    def _submit_plan_job(self, message, expected_status=202):
        status, payload = self._post(
            "/v1/plan-jobs",
            "analysis.plan.submit",
            dict(self.identity, message=message),
        )
        self.assertEqual(status, expected_status)
        return payload

    def _wait_for_terminal(self, job_id):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            payload = self._get_plan_job(job_id)
            if payload["state"] not in {"queued", "running"}:
                return payload
            time.sleep(0.01)
        self.fail("planning job did not reach a terminal state")

    def _get_plan_job(self, job_id, identity=None):
        query = urlencode(identity or self.identity)
        request = Request(
            "http://127.0.0.1:{}/v1/plan-jobs/{}?{}".format(
                self.server.server_port, job_id, query
            ),
            method="GET",
        )
        with urlopen(request, timeout=2) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        return envelope["payload"]

    def _cancel_plan_job(self, job_id, identity=None):
        status, payload = self._post(
            "/v1/plan-jobs/{}/cancel".format(job_id),
            "analysis.plan.cancel",
            identity or self.identity,
        )
        self.assertEqual(status, 200)
        return payload

    def _post(self, path, action, payload):
        request_id = "req-" + action
        request = Request(
            "http://127.0.0.1:{}{}".format(self.server.server_port, path),
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
            status = response.status
        self.assertEqual(envelope["request_id"], request_id)
        return status, envelope["payload"]


if __name__ == "__main__":
    unittest.main()
