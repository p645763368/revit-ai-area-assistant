import json
from pathlib import Path
import tempfile
import unittest

from area_assistant_agent.model_diagnostics import persist_model_diagnostic
from area_assistant_agent.model_protocol import summarize_chat_completion_shape


class ModelDiagnosticsTests(unittest.TestCase):
    def test_rejects_raw_provider_payload_before_creating_diagnostic_file(self):
        raw_payload = {
            "id": "SECRET_KEY",
            "choices": [{"message": {
                "content": "SECRET_KEY",
                "reasoning_content": "SECRET_KEY",
                "tool_calls": [{"function": {
                    "name": "inspect_secret",
                    "arguments": {"revit_path": "C:\\Secret.rvt", "token": "token-value"},
                }}],
                "authorization": "token-value",
            }}],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_directory = Path(temporary_directory)

            with self.assertRaises(ValueError):
                persist_model_diagnostic(
                    session_directory,
                    "job-123",
                    "diagnostic_123",
                    raw_payload,
                )

            self.assertFalse((session_directory / "model_diagnostics").exists())

    def test_persists_one_value_free_jsonl_record_atomically(self):
        shape = summarize_chat_completion_shape({"choices": []})
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_directory = Path(temporary_directory)

            path = persist_model_diagnostic(
                session_directory,
                "job-123",
                "diagnostic_123",
                shape,
            )

            self.assertEqual(
                path, session_directory / "model_diagnostics" / "protocol.jsonl"
            )
            records = [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["job_id"], "job-123")
        self.assertEqual(records[0]["diagnostic_id"], "diagnostic_123")
        self.assertIn("timestamp", records[0])
        self.assertEqual(records[0]["shape"], shape)
        self.assertEqual(set(records[0]), {"job_id", "diagnostic_id", "timestamp", "shape"})


if __name__ == "__main__":
    unittest.main()
