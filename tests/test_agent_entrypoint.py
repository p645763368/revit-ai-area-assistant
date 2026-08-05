import json
import subprocess
import sys
import unittest


class AgentEntrypointTests(unittest.TestCase):
    def test_check_reports_ready_with_contract_version(self):
        completed = subprocess.run(
            [sys.executable, "-m", "area_assistant_agent", "--check"],
            check=True,
            capture_output=True,
            text=True,
        )

        result = json.loads(completed.stdout)
        self.assertEqual(
            result,
            {
                "contract_version": "1.0",
                "service": "revit-ai-area-assistant-agent",
                "status": "ready",
            },
        )

    def test_document_status_rejects_request_outside_shared_v1_envelope(self):
        completed = subprocess.run(
            [sys.executable, "-m", "area_assistant_agent", "--document-status"],
            input=json.dumps({"request_id": "legacy", "current_document": {}}),
            capture_output=True,
            text=True,
        )

        result = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(result["message_type"], "error")
        self.assertIn("request envelope", result["message"])


if __name__ == "__main__":
    unittest.main()
