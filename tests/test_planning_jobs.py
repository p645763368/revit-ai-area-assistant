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
        job_ids = iter(("job-1", "job-2", "job-3", "job-4", "job-5"))
        self.registry = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:00:00+00:00",
            id_factory=lambda: next(job_ids),
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

    def _write_legacy_duplicate_records(self):
        job_ids = iter(("job-z", "job-a"))
        legacy_registry = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:00:00+00:00",
            id_factory=lambda: next(job_ids),
        )
        stale, _ = legacy_registry.submit(self.identity, "legacy scan")
        legacy_registry.transition(stale["job_id"], "running", "reading_model")
        legacy_registry.transition(
            stale["job_id"],
            "failed",
            "finished",
            error={
                "code": "old_failure",
                "message": "Old terminal error must not be replayed.",
                "retryable": True,
            },
        )
        replacement, _ = legacy_registry.submit(
            self.identity, "legacy scan", retry_terminal=True
        )
        legacy_registry.transition(
            replacement["job_id"], "running", "reading_model"
        )
        legacy_registry.transition(
            replacement["job_id"],
            "completed",
            "finished",
            result=self._valid_result("Old completed result must not be replayed."),
        )
        for path in self.storage_root.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            record.pop("idempotency_generation")
            path.write_text(json.dumps(record), encoding="utf-8")
        return stale["job_id"], replacement["job_id"]

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

    def test_interrupted_snapshot_survives_two_consecutive_registry_recreations(self):
        job, _ = self.registry.submit(self.identity, "scan")
        self.registry.transition(job["job_id"], "running", "reading_model")

        first_restart = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:01:00+00:00",
            id_factory=lambda: "job-2",
        )
        first_snapshot = first_restart.get(job["job_id"], self.identity)
        second_restart = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:02:00+00:00",
            id_factory=lambda: "job-3",
        )
        second_snapshot = second_restart.get(job["job_id"], self.identity)

        self.assertEqual(second_snapshot, first_snapshot)
        self.assertEqual(second_snapshot["state"], "interrupted")
        self.assertEqual(second_snapshot["error"]["code"], "agent_restarted")

    def test_explicit_retry_replaces_interrupted_index_once_after_restart(self):
        job, _ = self.registry.submit(self.identity, "scan")
        self.registry.transition(job["job_id"], "running", "reading_model")
        restored_ids = iter(("job-2", "job-3"))
        restored_registry = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:01:00+00:00",
            id_factory=lambda: next(restored_ids),
        )

        replayed, replayed_created = restored_registry.submit(
            self.identity, "scan", retry_terminal=False
        )
        retried, retried_created = restored_registry.submit(
            self.identity, "scan", retry_terminal=True
        )
        duplicate_retry, duplicate_created = restored_registry.submit(
            self.identity, "scan", retry_terminal=True
        )

        self.assertEqual(replayed["job_id"], job["job_id"])
        self.assertEqual(replayed["state"], "interrupted")
        self.assertFalse(replayed_created)
        self.assertNotEqual(retried["job_id"], job["job_id"])
        self.assertEqual(retried["state"], "queued")
        self.assertTrue(retried_created)
        self.assertEqual(duplicate_retry["job_id"], retried["job_id"])
        self.assertFalse(duplicate_created)

    def test_lost_terminal_retry_response_then_restart_dedups_replacement(self):
        old_job, _ = self.registry.submit(self.identity, "restart scan")
        self.registry.cancel(old_job["job_id"], self.identity)
        replacement, replacement_created = self.registry.submit(
            self.identity, "restart scan", retry_terminal=True
        )
        # Recreating before the recovery submit simulates the Agent accepting
        # the explicit retry, losing its response, and then restarting.
        restored = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:01:00+00:00",
            id_factory=lambda: "job-3",
        )
        recovered, recovered_created = restored.submit(
            self.identity, "restart scan", retry_terminal=False
        )
        persisted = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in self.storage_root.glob("*.json")
        }

        self.assertTrue(replacement_created)
        self.assertLess(old_job["job_id"], replacement["job_id"])
        self.assertEqual(recovered["job_id"], replacement["job_id"])
        self.assertEqual(recovered["state"], "interrupted")
        self.assertFalse(recovered_created)
        self.assertEqual(len(persisted), 2)
        self.assertEqual(persisted[old_job["job_id"]]["idempotency_generation"], 0)
        self.assertEqual(
            persisted[replacement["job_id"]]["idempotency_generation"], 1
        )

    def test_legacy_duplicate_records_fail_closed_and_retry_only_once(self):
        stale_id, replacement_id = self._write_legacy_duplicate_records()
        restored_ids = iter(("job-fresh", "job-unused"))
        restored = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:01:00+00:00",
            id_factory=lambda: next(restored_ids),
        )
        safe_error = {
            "code": "legacy_idempotency_conflict",
            "message": "Conflicting legacy planning jobs were interrupted safely.",
            "retryable": True,
        }

        stale = restored.get(stale_id, self.identity)
        replacement = restored.get(replacement_id, self.identity)
        replayed, replayed_created = restored.submit(
            self.identity, "legacy scan", retry_terminal=False
        )
        files_after_replay = list(self.storage_root.glob("*.json"))
        retried, retried_created = restored.submit(
            self.identity, "legacy scan", retry_terminal=True
        )
        duplicate_retry, duplicate_created = restored.submit(
            self.identity, "legacy scan", retry_terminal=True
        )
        persisted = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in self.storage_root.glob("*.json")
        }

        for snapshot in (stale, replacement, replayed):
            self.assertEqual(snapshot["state"], "interrupted")
            self.assertEqual(snapshot["stage"], "finished")
            self.assertIsNone(snapshot["result"])
            self.assertEqual(snapshot["error"], safe_error)
            self.assertEqual(
                set(snapshot),
                {
                    "job_id",
                    "state",
                    "stage",
                    "created_at",
                    "updated_at",
                    "result",
                    "error",
                },
            )
        self.assertEqual(replayed["job_id"], "job-z")
        self.assertFalse(replayed_created)
        self.assertEqual(len(files_after_replay), 2)
        self.assertTrue(retried_created)
        self.assertEqual(retried["job_id"], "job-fresh")
        self.assertEqual(retried["state"], "queued")
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate_retry, retried)
        self.assertEqual(len(persisted), 3)
        self.assertEqual(persisted[stale_id]["idempotency_generation"], 0)
        self.assertEqual(persisted[replacement_id]["idempotency_generation"], 0)
        self.assertEqual(persisted[retried["job_id"]]["idempotency_generation"], 1)

    def test_legacy_conflict_write_failure_publishes_no_partial_registry(self):
        stale_id, replacement_id = self._write_legacy_duplicate_records()

        class FailingMigrationRegistry(PlanningJobRegistry):
            def __init__(self, *args, **kwargs):
                self.migration_write_count = 0
                super().__init__(*args, **kwargs)

            def _write_record(self, record):
                self.migration_write_count += 1
                if self.migration_write_count == 2:
                    raise OSError("injected legacy migration failure")
                return super()._write_record(record)

        failed_registry = FailingMigrationRegistry.__new__(FailingMigrationRegistry)
        with self.assertRaisesRegex(OSError, "injected legacy migration failure"):
            failed_registry.__init__(
                self.storage_root,
                clock=lambda: "2026-08-21T10:01:00+00:00",
            )

        self.assertEqual(failed_registry._jobs, {})
        self.assertEqual(failed_registry._idempotency_index, {})
        partially_written = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in self.storage_root.glob("*.json")
        ]
        self.assertTrue(
            all("idempotency_generation" not in record for record in partially_written)
        )

        recovered = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:02:00+00:00",
        )
        for job_id in (stale_id, replacement_id):
            snapshot = recovered.get(job_id, self.identity)
            self.assertEqual(snapshot["state"], "interrupted")
            self.assertIsNone(snapshot["result"])
            self.assertEqual(
                snapshot["error"],
                {
                    "code": "legacy_idempotency_conflict",
                    "message": (
                        "Conflicting legacy planning jobs were interrupted safely."
                    ),
                    "retryable": True,
                },
            )

    def test_explicit_retry_never_replaces_completed_job(self):
        job, _ = self.registry.submit(self.identity, "scan")
        self.registry.transition(job["job_id"], "running", "reading_model")
        completed = self.registry.transition(
            job["job_id"], "completed", "finished", result=self._valid_result()
        )

        retried, created = self.registry.submit(
            self.identity, "scan", retry_terminal=True
        )

        self.assertFalse(created)
        self.assertEqual(retried, completed)

    def test_explicit_retry_replaces_failed_and_cancelled_jobs(self):
        failed, _ = self.registry.submit(self.identity, "fail scan")
        self.registry.transition(failed["job_id"], "running", "requesting_model")
        self.registry.transition(
            failed["job_id"],
            "failed",
            "finished",
            error={
                "code": "planning_failed",
                "message": "Planning failed.",
                "retryable": True,
            },
        )
        cancelled, _ = self.registry.submit(self.identity, "cancel scan")
        self.registry.cancel(cancelled["job_id"], self.identity)

        retried_failed, failed_created = self.registry.submit(
            self.identity, "fail scan", retry_terminal=True
        )
        retried_cancelled, cancelled_created = self.registry.submit(
            self.identity, "cancel scan", retry_terminal=True
        )

        self.assertTrue(failed_created)
        self.assertNotEqual(retried_failed["job_id"], failed["job_id"])
        self.assertTrue(cancelled_created)
        self.assertNotEqual(retried_cancelled["job_id"], cancelled["job_id"])

    def test_retry_terminal_must_be_an_exact_boolean(self):
        with self.assertRaises(ValueError):
            self.registry.submit(self.identity, "scan", retry_terminal=1)

    def test_malformed_and_unsafe_job_files_are_skipped(self):
        valid_job, _ = self.registry.submit(self.identity, "valid scan")
        (self.storage_root / "malformed.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        unsafe_record = {
            "job_id": "unsafe",
            "state": "completed",
            "stage": "finished",
            "created_at": "2026-08-21T10:00:00+00:00",
            "updated_at": "2026-08-21T10:00:00+00:00",
            "result": {"raw_model_response": "model-secret"},
            "error": None,
            "identity": self.identity,
            "idempotency_key": "unsafe-key",
        }
        (self.storage_root / "unsafe.json").write_text(
            json.dumps(unsafe_record), encoding="utf-8"
        )

        restored = PlanningJobRegistry(
            self.storage_root,
            clock=lambda: "2026-08-21T10:01:00+00:00",
            id_factory=lambda: "job-2",
        )

        self.assertIsNotNone(restored.get(valid_job["job_id"], self.identity))
        self.assertIsNone(restored.get("unsafe", self.identity))

    def test_malformed_idempotency_generations_are_isolated(self):
        valid_job, _ = self.registry.submit(self.identity, "valid scan")
        valid_path = next(self.storage_root.glob("*.json"))
        valid_record = json.loads(valid_path.read_text(encoding="utf-8"))
        for job_id, generation in (
            ("negative-generation", -1),
            ("boolean-generation", True),
            ("string-generation", "1"),
        ):
            malformed = dict(
                valid_record,
                job_id=job_id,
                idempotency_generation=generation,
            )
            (self.storage_root / (job_id + ".json")).write_text(
                json.dumps(malformed), encoding="utf-8"
            )

        restored = PlanningJobRegistry(self.storage_root)

        self.assertIsNotNone(restored.get(valid_job["job_id"], self.identity))
        self.assertIsNone(restored.get("negative-generation", self.identity))
        self.assertIsNone(restored.get("boolean-generation", self.identity))
        self.assertIsNone(restored.get("string-generation", self.identity))

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
                "idempotency_generation",
                "idempotency_key",
            },
        )
        self.assertEqual(persisted["idempotency_generation"], 0)
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
            "Authorization: Bearer short password=result-password"
        )
        job, _ = self.registry.submit(self.identity, "scan")
        self.registry.transition(job["job_id"], "running", "reading_model")

        completed = self.registry.transition(
            job["job_id"], "completed", "finished", result=result
        )
        persisted_text = next(self.storage_root.glob("*.json")).read_text(
            encoding="utf-8"
        )

        self.assertNotIn("short", persisted_text)
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
                    "Authorization: Bearer brief "
                    "password=error-password"
                ),
                "retryable": True,
            },
        )
        all_persisted_text = "\n".join(
            path.read_text(encoding="utf-8") for path in self.storage_root.glob("*.json")
        )

        self.assertEqual(set(failed["error"]), {"code", "message", "retryable"})
        self.assertNotIn("brief", all_persisted_text)
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
