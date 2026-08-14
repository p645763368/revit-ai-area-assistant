import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen


class AgentEntrypointTests(unittest.TestCase):
    def test_check_reports_ready_with_contract_version(self):
        completed = subprocess.run(
            [sys.executable, "-m", "area_assistant_agent", "--check"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(completed.stdout),
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

    def test_serve_reuses_the_single_agent_already_bound_to_the_loopback_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = dict(os.environ)
        environment["AI_AREA_ASSISTANT_PORT"] = str(port)
        first = subprocess.Popen(
            [sys.executable, "-m", "area_assistant_agent", "--serve"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 3
            while True:
                try:
                    with urlopen("http://127.0.0.1:{}/health".format(port), timeout=0.2):
                        break
                except URLError:
                    if time.monotonic() >= deadline:
                        self.fail("Agent did not expose health endpoint in time")
                    time.sleep(0.05)
            second = subprocess.run(
                [sys.executable, "-m", "area_assistant_agent", "--serve"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(json.loads(second.stdout)["status"], "already-running")
            self.assertIsNone(first.poll())
        finally:
            first.terminate()
            first.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
