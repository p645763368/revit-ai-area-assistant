import json
from pathlib import Path
import subprocess
import sys
import tempfile
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

    def test_show_data_root_reports_the_resolved_external_directory(self):
        with tempfile.TemporaryDirectory() as project_directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "area_assistant_agent",
                    "--show-data-root",
                    project_directory,
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            result = json.loads(completed.stdout)
            self.assertEqual(
                Path(result["data_root"]),
                (Path(project_directory) / "AI_Area_Assistant_Data").resolve(),
            )
            self.assertTrue(Path(result["data_root"]).is_absolute())


if __name__ == "__main__":
    unittest.main()
