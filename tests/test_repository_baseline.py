import runpy
import subprocess
import sys
import types
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.check_repository_safety import violations


ROOT = Path(__file__).resolve().parents[1]


class RepositoryBaselineTests(unittest.TestCase):
    def test_pyrevit_command_is_present_and_valid_python(self):
        script = ROOT / "pyrevit" / "AI Area Assistant.extension" / "AI Area Assistant.tab" / "Assistant.panel" / "Open.pushbutton" / "script.py"
        alerts = []
        fake_forms = types.SimpleNamespace(alert=lambda message, **options: alerts.append((message, options)))
        fake_pyrevit = types.ModuleType("pyrevit")
        fake_pyrevit.forms = fake_forms
        fake_pyrevit.revit = types.SimpleNamespace(
            doc=types.SimpleNamespace(
                Title="Development Copy",
                PathName=r"D:\RevitTests\area-assistant-development-copy.rvt",
                IsModified=True,
                ActiveView=types.SimpleNamespace(Id=2178223, Name="GFA Review"),
                ProjectInformation=types.SimpleNamespace(UniqueId="project-information-id"),
            )
        )

        with patch.dict(sys.modules, {"pyrevit": fake_pyrevit}), patch.dict(
            "os.environ",
            {"AI_AREA_ASSISTANT_TEST_DOCUMENT": r"D:\RevitTests\area-assistant-development-copy.rvt"},
        ):
            runpy.run_path(str(script))

        self.assertEqual(len(alerts), 1)
        message, options = alerts[0]
        self.assertIn("Revit instance: revit-", message)
        self.assertIn(r"Document path: D:\RevitTests\area-assistant-development-copy.rvt", message)
        self.assertIn("Active view: GFA Review (2178223)", message)
        self.assertIn("IsModified: True", message)
        self.assertIn("Authorized path match: yes", message)
        self.assertIn("Agent/rvt-mcp binding: pending", message)
        self.assertIn("Write permission: denied", message)
        self.assertEqual(options["title"], "AI Area Assistant")

    def test_sensitive_runtime_artifacts_are_ignored(self):
        sensitive_paths = [
            "sample.rvt",
            ".env",
            "project.log",
            "screenshot.png",
            "AI_Area_Assistant_Data/state.json",
        ]

        completed = subprocess.run(
            ["git", "check-ignore", *sensitive_paths],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(completed.stdout.splitlines(), sensitive_paths)

    def test_tracked_files_pass_forbidden_artifact_and_secret_scan(self):
        completed = subprocess.run(
            [sys.executable, "scripts/check_repository_safety.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_safety_scan_rejects_forced_root_screenshot(self):
        self.assertEqual(
            violations([Path("screenshot-demo.png")]),
            ["forbidden artifact: screenshot-demo.png"],
        )


if __name__ == "__main__":
    unittest.main()
