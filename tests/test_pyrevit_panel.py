import importlib
import sys
import types
import unittest
from unittest.mock import patch


class _Control:
    def __init__(self):
        self.IsEnabled = False
        self.Text = ""

    def AppendText(self, text):
        self.Text += text


class _InterruptedClient:
    def stream_chat(self, message):
        yield {
            "message_type": "response",
            "status": "accepted",
            "payload": {"delta": "部分回复"},
        }
        yield {
            "message_type": "error",
            "code": "model_protocol_error",
            "message": "Model API returned an incompatible streaming response.",
            "retryable": True,
        }


def _load_panel_module():
    fake_ui = types.ModuleType("Autodesk.Revit.UI")
    fake_ui.DockablePaneState = type("DockablePaneState", (), {})
    fake_ui.DockPosition = types.SimpleNamespace(Right="Right")
    fake_revit = types.ModuleType("Autodesk.Revit")
    fake_revit.UI = fake_ui
    fake_autodesk = types.ModuleType("Autodesk")
    fake_autodesk.Revit = fake_revit

    fake_system = types.ModuleType("System")
    fake_system.Action = lambda callback: callback
    fake_forms = types.SimpleNamespace(WPFPanel=object)
    fake_pyrevit = types.ModuleType("pyrevit")
    fake_pyrevit.forms = fake_forms
    fake_process = types.ModuleType("area_assistant_pyrevit.process")
    fake_process.start_agent_process = lambda repository_root: None
    modules = {
        "Autodesk": fake_autodesk,
        "Autodesk.Revit": fake_revit,
        "Autodesk.Revit.UI": fake_ui,
        "System": fake_system,
        "pyrevit": fake_pyrevit,
        "area_assistant_pyrevit.process": fake_process,
    }
    sys.modules.pop("area_assistant_pyrevit.panel", None)
    with patch.dict(sys.modules, modules):
        return importlib.import_module("area_assistant_pyrevit.panel")


class PyRevitPanelTests(unittest.TestCase):
    def test_interrupted_reply_unblocks_panel_and_enables_retry(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._client = _InterruptedClient()
        panel._dispatch = lambda callback: callback()
        panel.Transcript = _Control()
        panel.SendButton = _Control()
        panel.RetryButton = _Control()
        panel.ConnectionState = _Control()
        panel.ConnectionDetail = _Control()

        panel._stream("hello")

        self.assertIn("部分回复", panel.Transcript.Text)
        self.assertIn("[错误]", panel.Transcript.Text)
        self.assertTrue(panel.SendButton.IsEnabled)
        self.assertTrue(panel.RetryButton.IsEnabled)
        self.assertEqual(panel.ConnectionState.Text, "请求失败")
        self.assertNotEqual(panel.ConnectionDetail.Text, "回复完成")


if __name__ == "__main__":
    unittest.main()
