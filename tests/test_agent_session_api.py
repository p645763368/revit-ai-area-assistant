import json
from pathlib import Path
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest

from area_assistant_agent.config import AgentConfig
from area_assistant_agent.persistence import SessionRepository
from area_assistant_agent.server import create_server


class AgentSessionApiTests(unittest.TestCase):
    def setUp(self):
        config = AgentConfig(
            host="127.0.0.1",
            port=0,
            base_url="http://127.0.0.1:1/v1",
            api_key="unit-test",
            model="test-model",
            timeout_seconds=1,
        )
        self.server = create_server(config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.panel_id = "panel-test"
        self.generation = 1

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_open_only_lists_current_document_and_writes_nothing_before_choice(self):
        with tempfile.TemporaryDirectory() as project_directory:
            repository = SessionRepository(Path(project_directory))
            original = repository.create_session("document-a")
            repository.create_session("document-b")
            before = original.state_path.read_bytes()

            opened = self._open_session(project_directory, "document-a")

            self.assertTrue(opened["requires_user_choice"])
            self.assertIsNone(opened["active_session_id"])
            self.assertEqual(
                [item["session_id"] for item in opened["sessions"]],
                [original.session_id],
            )
            self.assertEqual(original.state_path.read_bytes(), before)
            session_directories = list(
                (Path(opened["data_root"]) / "documents").glob("*/sessions/*")
            )
            self.assertEqual(len(session_directories), 2)

    def test_new_document_does_not_create_storage_until_user_chooses_new(self):
        with tempfile.TemporaryDirectory() as project_directory:
            opened = self._open_session(project_directory, "document-a")

            self.assertEqual(opened["sessions"], [])
            self.assertFalse(Path(opened["data_root"]).exists())

            created = self._post(
                "/v1/sessions/choose",
                {
                    "choice": "new",
                    "context_id": opened["context_id"],
                    "document_fingerprint": "document-a",
                    "generation": self.generation,
                    "panel_instance_id": self.panel_id,
                    "project_directory": project_directory,
                    "session_id": None,
                },
            )

            self.assertEqual(created["status"], "idle")
            self.assertTrue(Path(created["data_root"]).exists())

    def test_explicit_continue_waits_for_new_action_and_message_is_audited(self):
        with tempfile.TemporaryDirectory() as project_directory:
            repository = SessionRepository(Path(project_directory))
            original = repository.create_session("document-a")
            opened = self._open_session(project_directory, "document-a")

            continued = self._post(
                "/v1/sessions/choose",
                {
                    "choice": "continue",
                    "context_id": opened["context_id"],
                    "document_fingerprint": "document-a",
                    "generation": self.generation,
                    "panel_instance_id": self.panel_id,
                    "project_directory": project_directory,
                    "session_id": original.session_id,
                },
            )
            recorded = self._post(
                "/v1/sessions/messages",
                {
                    "content": "new explicit action",
                    "context_id": continued["context_id"],
                    "document_fingerprint": "document-a",
                    "generation": self.generation,
                    "panel_instance_id": self.panel_id,
                    "project_directory": project_directory,
                    "role": "user",
                    "session_id": original.session_id,
                },
            )

            self.assertEqual(continued["status"], "awaiting_user_action")
            self.assertTrue(recorded["recorded"])
            conversation = (original.directory / "conversation.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("new explicit action", conversation)

    def test_opening_another_document_invalidates_late_writes_from_the_old_one(self):
        with tempfile.TemporaryDirectory() as project_directory:
            opened_a = self._open_session(project_directory, "document-a")
            chosen_a = self._post(
                "/v1/sessions/choose",
                {
                    "choice": "new",
                    "context_id": opened_a["context_id"],
                    "document_fingerprint": "document-a",
                    "generation": self.generation,
                    "panel_instance_id": self.panel_id,
                    "project_directory": project_directory,
                    "session_id": None,
                },
            )
            self._post(
                "/v1/sessions/revoke",
                {
                    "context_id": chosen_a["context_id"],
                    "generation": 2,
                    "panel_instance_id": self.panel_id,
                },
            )

            with self.assertRaises(HTTPError) as raised:
                self._post(
                    "/v1/sessions/messages",
                    {
                        "content": "late reply for A",
                        "context_id": chosen_a["context_id"],
                        "document_fingerprint": "document-a",
                        "generation": self.generation,
                        "panel_instance_id": self.panel_id,
                        "project_directory": project_directory,
                        "role": "assistant",
                        "session_id": chosen_a["active_session_id"],
                    },
                )
            self.assertEqual(raised.exception.code, 400)

            with self.assertRaises(HTTPError):
                self._open_session(project_directory, "document-a")

            conversation_paths = list(
                Path(project_directory).glob(
                    "AI_Area_Assistant_Data/documents/*/sessions/{}/conversation.jsonl".format(
                        chosen_a["active_session_id"]
                    )
                )
            )
            self.assertEqual(conversation_paths, [])

    def test_revoke_fence_rejects_checked_late_write_without_touching_jsonl(self):
        with tempfile.TemporaryDirectory() as project_directory:
            opened = self._open_session(project_directory, "document-a")
            chosen = self._post(
                "/v1/sessions/choose",
                {
                    "choice": "new",
                    "context_id": opened["context_id"],
                    "document_fingerprint": "document-a",
                    "generation": self.generation,
                    "panel_instance_id": self.panel_id,
                    "project_directory": project_directory,
                    "session_id": None,
                },
            )
            message = {
                "content": "initial message",
                "context_id": chosen["context_id"],
                "document_fingerprint": "document-a",
                "generation": self.generation,
                "panel_instance_id": self.panel_id,
                "project_directory": project_directory,
                "role": "user",
                "session_id": chosen["active_session_id"],
            }
            self._post("/v1/sessions/messages", message)
            conversation_path = next(
                Path(project_directory).glob(
                    "AI_Area_Assistant_Data/documents/*/sessions/{}/conversation.jsonl".format(
                        chosen["active_session_id"]
                    )
                )
            )
            before_content = conversation_path.read_bytes()
            before_mtime = conversation_path.stat().st_mtime_ns
            local_check_passed = threading.Event()
            allow_late_request = threading.Event()
            results = []

            def send_checked_late_reply():
                local_check_passed.set()
                allow_late_request.wait(2)
                late_message = dict(message)
                late_message.update(
                    {"content": "late assistant reply", "role": "assistant"}
                )
                try:
                    self._post("/v1/sessions/messages", late_message)
                except HTTPError as error:
                    results.append(error.code)

            writer = threading.Thread(target=send_checked_late_reply)
            writer.start()
            self.assertTrue(local_check_passed.wait(1))
            self._post(
                "/v1/sessions/revoke",
                {
                    "context_id": chosen["context_id"],
                    "generation": 2,
                    "panel_instance_id": self.panel_id,
                },
            )
            allow_late_request.set()
            writer.join(2)

            self.assertFalse(writer.is_alive())
            self.assertEqual(results, [400])
            self.assertEqual(conversation_path.read_bytes(), before_content)
            self.assertEqual(conversation_path.stat().st_mtime_ns, before_mtime)

    def _open_session(self, project_directory, document_fingerprint):
        return self._post(
            "/v1/sessions/open",
            {
                "project_directory": project_directory,
                "document_fingerprint": document_fingerprint,
                "generation": self.generation,
                "panel_instance_id": self.panel_id,
            },
        )

    def _post(self, path, payload):
        actions = {
            "/v1/sessions/open": "session.open",
            "/v1/sessions/choose": "session.choose",
            "/v1/sessions/messages": "session.message",
            "/v1/sessions/revoke": "session.revoke",
        }
        request_id = "req-session-test"
        envelope = {
            "contract_version": "1.0",
            "message_type": "request",
            "request_id": request_id,
            "action": actions[path],
            "payload": payload,
        }
        request = Request(
            "http://127.0.0.1:{}{}".format(self.server.server_port, path),
            data=json.dumps(envelope).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        self.assertEqual(envelope["contract_version"], "1.0")
        self.assertEqual(envelope["message_type"], "response")
        self.assertEqual(envelope["request_id"], request_id)
        return envelope["payload"]


if __name__ == "__main__":
    unittest.main()
