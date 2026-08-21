import json
from pathlib import Path
import tempfile
import unittest

from area_assistant_agent.planning_jobs import PlanningJobRegistry


class PlanningJobRegistryTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.storage_root = Path(self._temporary_directory.name)
        self.identity = {
            "panel_instance_id": "panel-a",
            "generation": 7,
            "context_id": "context-a",
            "document_fingerprint": "document-a",
            "session_id": "session-a",
        }
        self.registry = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:00:00+00:00",
            id_factory=lambda: "job-1",
        )

    @staticmethod
    def _valid_result(summary="done"):
        return {
            "summary": summary,
            "question": "Which source should be compared next?",
            "options": [
                {
                    "id": "floor",
                    "label": "Use floor",
                    "recommended": True,
                    "rationale": "Complete profile is available",
                    "impact": "Continue with the floor profile",
                },
                {
                    "id": "walls",
                    "label": "Use walls",
                    "recommended": False,
                    "rationale": "Useful cross-check",
                    "impact": "Check wall joins",
                },
            ],
        }

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_duplicate_active_submission_reuses_one_job(self):
        first, first_created = self.registry.submit(self.identity, "  scan   model ")
        second, second_created = self.registry.submit(self.identity, "scan model")

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["state"], "queued")
        self.assertEqual(first["stage"], "accepted")

    def test_transition_follows_queued_running_completed(self):
        job, _ = self.registry.submit(self.identity, "scan")

        running = self.registry.transition(job["job_id"], "running", "reading_model")
        completed = self.registry.transition(
            job["job_id"],
            "completed",
            "finished",
            result=self._valid_result(),
        )

        self.assertEqual(running["state"], "running")
        self.assertEqual(running["stage"], "reading_model")
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["result"], self._valid_result())
        self.assertIsNone(completed["error"])

    def test_invalid_transition_is_rejected(self):
        job, _ = self.registry.submit(self.identity, "scan")

        with self.assertRaises(ValueError):
            self.registry.transition(job["job_id"], "completed", "finished")

        self.registry.transition(job["job_id"], "running", "reading_model")
        with self.assertRaises(ValueError):
            self.registry.transition(job["job_id"], "queued", "accepted")

    def test_get_and_cancel_hide_jobs_from_a_different_identity(self):
        job, _ = self.registry.submit(self.identity, "scan")
        other_identity = dict(self.identity, context_id="context-b")

        self.assertIsNone(self.registry.get(job["job_id"], other_identity))
        self.assertIsNone(self.registry.cancel(job["job_id"], other_identity))
        self.assertTrue(self.registry.is_active(job["job_id"]))

    def test_cancel_is_idempotent_and_deactivates_job(self):
        job, _ = self.registry.submit(self.identity, "scan")

        cancelled = self.registry.cancel(job["job_id"], self.identity)
        cancelled_again = self.registry.cancel(job["job_id"], self.identity)

        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(cancelled_again, cancelled)
        self.assertFalse(self.registry.is_active(job["job_id"]))

    def test_non_terminal_job_becomes_interrupted_after_registry_recreation(self):
        job, _ = self.registry.submit(self.identity, "scan")
        self.registry.transition(job["job_id"], "running", "reading_model")

        restored = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:01:00+00:00",
        ).get(job["job_id"], self.identity)

        self.assertEqual(restored["state"], "interrupted")
        self.assertEqual(restored["error"]["code"], "agent_restarted")

    def test_persisted_json_contains_only_snapshot_and_safe_request_metadata(self):
        job, _ = self.registry.submit(self.identity, "  scan   model ")
        job["result"] = {"tampered": True}

        persisted_path = next(self.storage_root.glob("*.json"))
        persisted = json.loads(persisted_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(persisted),
            {
                "job_id",
                "state",
                "stage",
                "created_at",
                "updated_at",
                "result",
                "error",
                "identity",
                "idempotency_key",
            },
        )
        self.assertEqual(persisted["identity"], self.identity)
        self.assertNotIn("scan", json.dumps(persisted))
        self.assertIsNone(self.registry.get(job["job_id"], self.identity)["result"])

    def test_returned_snapshots_are_defensive_copies(self):
        job, _ = self.registry.submit(self.identity, "scan")
        job["state"] = "completed"
        job["error"] = {"code": "tampered"}

        restored = self.registry.get(job["job_id"], self.identity)

        self.assertEqual(restored["state"], "queued")
        self.assertIsNone(restored["error"])

    def test_result_and_error_are_validated_and_redacted_before_persistence(self):
        result = self._valid_result(
            "Authorization: Bearer result-secret password=result-password"
        )
        job, _ = self.registry.submit(self.identity, "scan")
        self.registry.transition(job["job_id"], "running", "reading_model")

        completed = self.registry.transition(
            job["job_id"], "completed", "finished", result=result
        )
        persisted_text = next(self.storage_root.glob("*.json")).read_text(
            encoding="utf-8"
        )

        self.assertNotIn("result-secret", persisted_text)
        self.assertNotIn("result-password", persisted_text)
        self.assertIn("[REDACTED]", persisted_text)
        self.assertIn("[REDACTED]", completed["result"]["summary"])

        failed_registry = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:00:00+00:00",
            id_factory=lambda: "job-2",
        )
        failed_job, _ = failed_registry.submit(self.identity, "scan failed")
        failed_registry.transition(failed_job["job_id"], "running", "reading_model")
        failed = failed_registry.transition(
            failed_job["job_id"],
            "failed",
            "finished",
            error={
                "code": "planning_failed",
                "message": (
                    "Authorization: Bearer error-secret "
                    "password=error-password"
                ),
                "retryable": True,
            },
        )
        all_persisted_text = "\n".join(
            path.read_text(encoding="utf-8") for path in self.storage_root.glob("*.json")
        )

        self.assertEqual(set(failed["error"]), {"code", "message", "retryable"})
        self.assertNotIn("error-secret", all_persisted_text)
        self.assertNotIn("error-password", all_persisted_text)
        self.assertIn("[REDACTED]", failed["error"]["message"])

    def test_write_failure_does_not_publish_transition_or_cancel(self):
        job, _ = self.registry.submit(self.identity, "scan")
        original_write = self.registry._write_record

        def fail_write(record):
            raise OSError("injected persistence failure")

        self.registry._write_record = fail_write
        with self.assertRaises(OSError):
            self.registry.transition(job["job_id"], "running", "reading_model")

        after_transition_failure = self.registry.get(job["job_id"], self.identity)
        self.assertEqual(after_transition_failure["state"], "queued")
        self.assertEqual(after_transition_failure["stage"], "accepted")

        self.registry._write_record = original_write
        self.registry._write_record = fail_write
        with self.assertRaises(OSError):
            self.registry.cancel(job["job_id"], self.identity)

        after_cancel_failure = self.registry.get(job["job_id"], self.identity)
        self.assertEqual(after_cancel_failure["state"], "queued")
        self.assertEqual(after_cancel_failure["stage"], "accepted")


if __name__ == "__main__":
    unittest.main()
