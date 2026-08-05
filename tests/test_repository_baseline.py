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
            {
                "AI_AREA_ASSISTANT_AGENT_PYTHON": "",
                "AI_AREA_ASSISTANT_TEST_DOCUMENT": r"D:\RevitTests\area-assistant-development-copy.rvt",
            },
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

    def test_pyrevit_command_displays_verified_agent_binding_when_configured(self):
        script = ROOT / "pyrevit" / "AI Area Assistant.extension" / "AI Area Assistant.tab" / "Assistant.panel" / "Open.pushbutton" / "script.py"
        alerts = []
        fake_pyrevit = types.ModuleType("pyrevit")
        fake_pyrevit.forms = types.SimpleNamespace(
            alert=lambda message, **options: alerts.append((message, options))
        )
        fake_pyrevit.revit = types.SimpleNamespace(
            doc=types.SimpleNamespace(
                Title="Development Copy",
                PathName=r"D:\RevitTests\area-assistant-development-copy.rvt",
                IsModified=False,
                ActiveView=types.SimpleNamespace(Id=42, Name="GFA Review"),
                ProjectInformation=types.SimpleNamespace(UniqueId="project-information-id"),
            )
        )
        fake_bridge = types.SimpleNamespace(
            query=lambda status, pause_reason=None: {
                "payload": {
                    "binding_status": "bound",
                    "rvt_mcp_status": "verified",
                    "write_allowed": True,
                    "pause_reason": None,
                }
            }
        )
        fake_bridge_module = types.ModuleType("area_assistant_revit.agent_bridge")
        fake_bridge_module.get_agent_bridge = lambda python, root: fake_bridge

        with patch.dict(
            sys.modules,
            {
                "pyrevit": fake_pyrevit,
                "area_assistant_revit.agent_bridge": fake_bridge_module,
            },
        ), patch.dict(
            "os.environ",
            {
                "AI_AREA_ASSISTANT_AGENT_PYTHON": sys.executable,
                "AI_AREA_ASSISTANT_TEST_DOCUMENT": r"D:\RevitTests\area-assistant-development-copy.rvt",
            },
        ):
            runpy.run_path(str(script))

        message, _ = alerts[0]
        self.assertIn("Agent/rvt-mcp binding: bound", message)
        self.assertIn("rvt-mcp status: verified", message)
        self.assertIn("Write permission: allowed", message)

    def test_pyrevit_background_verification_does_not_show_modal_pending_alert(self):
        script = ROOT / "pyrevit" / "AI Area Assistant.extension" / "AI Area Assistant.tab" / "Assistant.panel" / "Open.pushbutton" / "script.py"
        alerts = []
        toasts = []
        fake_pyrevit = types.ModuleType("pyrevit")
        fake_pyrevit.forms = types.SimpleNamespace(
            alert=lambda message, **options: alerts.append((message, options)),
            toast=lambda message, **options: toasts.append((message, options)),
        )
        fake_pyrevit.revit = types.SimpleNamespace(
            doc=types.SimpleNamespace(
                Title="Development Copy",
                PathName=r"D:\RevitTests\area-assistant-development-copy.rvt",
                IsModified=False,
                ActiveView=types.SimpleNamespace(Id=42, Name="GFA Review"),
                ProjectInformation=types.SimpleNamespace(UniqueId="project-information-id"),
            )
        )
        fake_bridge_module = types.ModuleType("area_assistant_revit.agent_bridge")
        fake_bridge_module.get_agent_bridge = lambda python, root: types.SimpleNamespace(
            query=lambda status, pause_reason=None: None
        )

        with patch.dict(
            sys.modules,
            {
                "pyrevit": fake_pyrevit,
                "area_assistant_revit.agent_bridge": fake_bridge_module,
            },
        ), patch.dict(
            "os.environ",
            {
                "AI_AREA_ASSISTANT_AGENT_PYTHON": sys.executable,
                "AI_AREA_ASSISTANT_TEST_DOCUMENT": r"D:\RevitTests\area-assistant-development-copy.rvt",
            },
        ):
            runpy.run_path(str(script))

        self.assertEqual(alerts, [])
        self.assertEqual(len(toasts), 1)
        self.assertIn("running in the background", toasts[0][0])

    def test_pyrevit_guard_marks_task_paused_when_active_document_changes(self):
        script_path = ROOT / "pyrevit" / "AI Area Assistant.extension" / "AI Area Assistant.tab" / "Assistant.panel" / "Open.pushbutton" / "script.py"
        envvars = {}

        class Event:
            def __init__(self):
                self.handler = None

            def __iadd__(self, handler):
                self.handler = handler
                return self

        class EventHandlerFactory:
            def __getitem__(self, event_type):
                return lambda handler: handler

        fake_event = Event()
        fake_script_api = types.SimpleNamespace(
            get_envvar=lambda key: envvars.get(key),
            set_envvar=lambda key, value: envvars.__setitem__(key, value),
        )
        fake_pyrevit = types.ModuleType("pyrevit")
        fake_pyrevit.forms = types.SimpleNamespace(alert=lambda message, **options: None)
        fake_pyrevit.revit = types.SimpleNamespace(
            doc=types.SimpleNamespace(
                Title="Development Copy",
                PathName=r"D:\RevitTests\area-assistant-development-copy.rvt",
                IsModified=False,
                ActiveView=types.SimpleNamespace(Id=42, Name="GFA Review"),
                ProjectInformation=types.SimpleNamespace(UniqueId="initial-project"),
            )
        )
        fake_pyrevit.script = fake_script_api
        fake_pyrevit.HOST_APP = types.SimpleNamespace(
            uiapp=types.SimpleNamespace(ViewActivated=fake_event)
        )
        fake_pyrevit.UI = types.SimpleNamespace(
            Events=types.SimpleNamespace(ViewActivatedEventArgs=object)
        )
        fake_pyrevit.framework = types.SimpleNamespace(EventHandler=EventHandlerFactory())
        fake_bridge_module = types.ModuleType("area_assistant_revit.agent_bridge")
        fake_bridge_module.get_agent_bridge = lambda python, root: types.SimpleNamespace(
            query=lambda status, pause_reason=None: {
                "payload": {
                    "binding_status": "bound",
                    "rvt_mcp_status": "verified",
                    "write_allowed": True,
                    "pause_reason": None,
                }
            }
        )

        with patch.dict(
            sys.modules,
            {
                "pyrevit": fake_pyrevit,
                "area_assistant_revit.agent_bridge": fake_bridge_module,
            },
        ), patch.dict(
            "os.environ",
            {
                "AI_AREA_ASSISTANT_AGENT_PYTHON": sys.executable,
                "AI_AREA_ASSISTANT_TEST_DOCUMENT": r"D:\RevitTests\area-assistant-development-copy.rvt",
            },
        ):
            runpy.run_path(str(script_path))
            switched_document = types.SimpleNamespace(
                Title="Another Model",
                PathName=r"D:\RevitTests\another-model.rvt",
                IsModified=False,
                ActiveView=types.SimpleNamespace(Id=99, Name="Floor Plan"),
                ProjectInformation=types.SimpleNamespace(UniqueId="another-project"),
            )
            fake_event.handler(
                None,
                types.SimpleNamespace(
                    CurrentActiveView=types.SimpleNamespace(Document=switched_document)
                ),
            )

        self.assertEqual(envvars["AI_AREA_ASSISTANT_DOCUMENT_PAUSE_REASON"], "document_changed")

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
