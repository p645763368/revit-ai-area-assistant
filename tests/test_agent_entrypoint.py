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


if __name__ == "__main__":
    unittest.main()
