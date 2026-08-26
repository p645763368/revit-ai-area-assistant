import json
from pathlib import Path
import tempfile
import unittest

from area_assistant_agent.model_diagnostics import persist_model_diagnostic


class ModelDiagnosticsTests(unittest.TestCase):
    def test_persists_one_value_free_jsonl_record_atomically(self):
        shape = {"top_level": {"type": "dict", "keys": ["choices"]}}
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
