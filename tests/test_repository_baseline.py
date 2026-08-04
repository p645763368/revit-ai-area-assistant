import subprocess
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryBaselineTests(unittest.TestCase):
    def test_pyrevit_command_is_present_and_valid_python(self):
        script = ROOT / "pyrevit" / "AI Area Assistant.extension" / "AI Area Assistant.tab" / "Assistant.panel" / "Open.pushbutton" / "script.py"
        source = script.read_text(encoding="utf-8")

        compile(source, str(script), "exec")
        self.assertIn("AI Area Assistant", source)

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


if __name__ == "__main__":
    unittest.main()
