import json
from pathlib import Path
import tempfile
import unittest

from area_assistant_agent.persistence import SessionRepository


class SessionPersistenceTests(unittest.TestCase):
    def test_new_session_writes_recoverable_state_and_readable_markdown(self):
        with tempfile.TemporaryDirectory() as project_directory:
            repository = SessionRepository(Path(project_directory))

            session = repository.create_session("document-fingerprint-a")

            self.assertTrue(repository.data_root.is_absolute())
            state = json.loads(session.state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["document_fingerprint"], "document-fingerprint-a")
            self.assertEqual(state["status"], "idle")
            self.assertFalse(state["model_operation_pending"])
            markdown = session.markdown_path.read_text(encoding="utf-8")
            self.assertIn("# AI Area Assistant session", markdown)
            self.assertIn(session.session_id, markdown)

    def test_recovery_choices_are_isolated_by_document_fingerprint(self):
        with tempfile.TemporaryDirectory() as project_directory:
            repository = SessionRepository(Path(project_directory))
            session_a = repository.create_session("document-fingerprint-a")
            repository.create_session("document-fingerprint-b")

            prompt = repository.recovery_prompt("document-fingerprint-a")

            self.assertTrue(prompt.requires_user_choice)
            self.assertEqual(
                [candidate.session_id for candidate in prompt.sessions],
                [session_a.session_id],
            )

    def test_explicit_resume_waits_for_a_new_user_action(self):
        with tempfile.TemporaryDirectory() as project_directory:
            repository = SessionRepository(Path(project_directory))
            original = repository.create_session("document-fingerprint-a")

            resumed = repository.resume_session(
                "document-fingerprint-a", original.session_id
            )

            state = json.loads(resumed.state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "awaiting_user_action")
            self.assertFalse(state["model_operation_pending"])
            self.assertIn(
                "No model action was replayed",
                resumed.markdown_path.read_text(encoding="utf-8"),
            )

    def test_conversation_is_recorded_as_jsonl_and_markdown(self):
        with tempfile.TemporaryDirectory() as project_directory:
            repository = SessionRepository(Path(project_directory))
            session = repository.create_session("document-fingerprint-a")

            repository.record_message(
                "document-fingerprint-a",
                session.session_id,
                role="user",
                content=(
                    "Continue the saved area review. "
                    "Authorization: Bearer conversation-secret"
                ),
            )

            records = [
                json.loads(line)
                for line in (session.directory / "conversation.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(records[0]["role"], "user")
            self.assertIn("Continue the saved area review.", records[0]["content"])
            self.assertNotIn("conversation-secret", records[0]["content"])
            self.assertIn(
                "Continue the saved area review.",
                session.markdown_path.read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                "conversation-secret",
                session.markdown_path.read_text(encoding="utf-8"),
            )

    def test_tool_errors_are_audited_without_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as project_directory:
            repository = SessionRepository(Path(project_directory))
            session = repository.create_session("document-fingerprint-a")

            repository.record_tool_event(
                "document-fingerprint-a",
                session.session_id,
                tool_name="inspect_document",
                inputs={
                    "Authorization": "Bearer top-secret",
                    "nested": {
                        "api_key": "sk-live-secret",
                        "level_id": 42,
                        "session_token": "token-value",
                    },
                },
                error="request failed: Authorization: Bearer error-secret",
            )

            for filename in ("operations.jsonl", "agent.log.jsonl"):
                text = (session.directory / filename).read_text(encoding="utf-8")
                event = json.loads(text)
                self.assertEqual(event["tool_name"], "inspect_document")
                self.assertEqual(event["event_type"], "tool_error")
                self.assertEqual(event["inputs"]["Authorization"], "[REDACTED]")
                self.assertEqual(event["inputs"]["nested"]["api_key"], "[REDACTED]")
                self.assertEqual(
                    event["inputs"]["nested"]["session_token"], "[REDACTED]"
                )
                self.assertEqual(event["inputs"]["nested"]["level_id"], 42)
                self.assertNotIn("top-secret", text)
                self.assertNotIn("live-secret", text)
                self.assertNotIn("error-secret", text)
            markdown = session.markdown_path.read_text(encoding="utf-8")
            self.assertNotIn("top-secret", markdown)
            self.assertNotIn("live-secret", markdown)
            self.assertNotIn("error-secret", markdown)

    def test_machine_state_can_be_loaded_by_a_new_repository_instance(self):
        with tempfile.TemporaryDirectory() as project_directory:
            project_path = Path(project_directory)
            repository = SessionRepository(project_path)
            session = repository.create_session("document-fingerprint-a")
            expected = {
                "selected_area_scheme_id": 101,
                "selected_level_ids": [201, 202, 203],
            }
            repository.save_machine_state(
                "document-fingerprint-a", session.session_id, expected
            )

            reopened_repository = SessionRepository(project_path)

            self.assertEqual(
                reopened_repository.load_machine_state(
                    "document-fingerprint-a", session.session_id
                ),
                expected,
            )


if __name__ == "__main__":
    unittest.main()
