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
    def record_message(self, *args):
        return {"recorded": True}

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


class _SessionClient:
    def __init__(self):
        self.calls = []

    def choose_session(
        self,
        project_directory,
        document_fingerprint,
        context_id,
        panel_instance_id,
        generation,
        choice,
        session_id,
    ):
        self.calls.append(("choose", document_fingerprint, choice, session_id))
        return {
            "active_session_id": session_id or "session-new",
            "context_id": context_id,
            "data_root": "C:\\test\\AI_Area_Assistant_Data",
            "status": "awaiting_user_action" if choice == "continue" else "idle",
        }

    def revoke_session(self, panel_instance_id, generation, context_id):
        self.calls.append(("revoke", panel_instance_id, generation, context_id))
        return {"revoked": True}


class _SwitchingClient:
    def __init__(self, panel):
        self.panel = panel
        self.records = []

    def record_message(
        self, project, fingerprint, context, panel, generation, session, role, content
    ):
        self.records.append((fingerprint, session, role, content))

    def revoke_session(self, panel_instance_id, generation, context_id):
        self.records.append(("revoked", context_id, generation))

    def stream_chat(self, message):
        self.panel._pause_session_for_document_change()
        yield {
            "message_type": "response",
            "status": "completed",
            "payload": {"message": "stale reply"},
        }


class _ObservingRevokeClient:
    def __init__(self, panel):
        self.panel = panel
        self.observed = None

    def revoke_session(self, panel_instance_id, generation, context_id):
        self.observed = (
            self.panel._session_id,
            self.panel._session_context_id,
            self.panel.SendButton.IsEnabled,
            self.panel._session_request_version,
        )
        return {"revoked": True}


def _load_panel_module():
    fake_ui = types.ModuleType("Autodesk.Revit.UI")
    fake_ui.DockablePaneState = type("DockablePaneState", (), {})
    fake_ui.DockPosition = types.SimpleNamespace(Right="Right")
    fake_ui.Events = types.SimpleNamespace(ViewActivatedEventArgs=object)
    fake_revit = types.ModuleType("Autodesk.Revit")
    fake_revit.UI = fake_ui
    fake_autodesk = types.ModuleType("Autodesk")
    fake_autodesk.Revit = fake_revit

    fake_system = types.ModuleType("System")
    fake_system.Action = lambda callback: callback
    fake_forms = types.SimpleNamespace(WPFPanel=object)
    fake_pyrevit = types.ModuleType("pyrevit")
    fake_pyrevit.forms = fake_forms
    fake_pyrevit.HOST_APP = types.SimpleNamespace(
        uiapp=types.SimpleNamespace(ViewActivated=None)
    )
    fake_pyrevit.framework = types.SimpleNamespace(EventHandler=object)
    fake_pyrevit.revit = types.SimpleNamespace(doc=None)
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
        panel._session_request_version = 1
        panel._panel_instance_id = "panel-a"
        panel._session_project_directory = "C:\\test"
        panel._session_document_fingerprint = "document-a"
        panel._session_context_id = "context-a"
        panel._session_id = "session-a"
        panel.Transcript = _Control()
        panel.SendButton = _Control()
        panel.RetryButton = _Control()
        panel.ConnectionState = _Control()
        panel.ConnectionDetail = _Control()

        panel._stream("hello", panel._session_context())

        self.assertIn("部分回复", panel.Transcript.Text)
        self.assertIn("[错误]", panel.Transcript.Text)
        self.assertTrue(panel.SendButton.IsEnabled)
        self.assertTrue(panel.RetryButton.IsEnabled)
        self.assertEqual(panel.ConnectionState.Text, "请求失败")
        self.assertNotEqual(panel.ConnectionDetail.Text, "回复完成")

    def test_latest_document_verification_updates_user_visible_safety_state(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._document_request_version = 2
        panel._document_pause_reason = None
        panel._run_background = lambda callback: callback()
        panel._bound_document_fingerprint = None
        panel._session_document_fingerprint = "fingerprint-1"
        panel._session_id = "session-a"
        panel._pending_session_id = None
        panel.DocumentState = _Control()
        panel.DocumentDetail = _Control()
        panel.RefreshDocumentButton = _Control()
        snapshot = {
            "revit_instance_id": "revit-19880",
            "document_path": r"D:\test\development-copy.rvt",
            "document_fingerprint": "fingerprint-1",
            "active_view": {"name": "Level 1", "id": 42},
            "is_modified": False,
            "authorized_path_match": True,
        }
        binding = {
            "binding_status": "bound",
            "rvt_mcp_status": "verified",
            "write_allowed": True,
            "pause_reason": None,
        }

        panel._document_verified(snapshot, binding, 2)

        self.assertEqual(panel.DocumentState.Text, "bound")
        self.assertIn("rvt-mcp：verified", panel.DocumentDetail.Text)
        self.assertIn("写入许可：allowed", panel.DocumentDetail.Text)
        self.assertIn("暂停原因：none", panel.DocumentDetail.Text)
        self.assertTrue(panel.RefreshDocumentButton.IsEnabled)

    def test_panel_waits_for_explicit_choice_without_writing_a_session(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._client = _SessionClient()
        panel._session_request_version = 4
        panel._panel_instance_id = "panel-a"
        panel._session_document_fingerprint = "document-a"
        panel._session_project_directory = "C:\\test"
        panel._session_id = None
        panel._session_context_id = None
        panel._pending_session_id = None
        panel._data_root = None
        panel.SendButton = _Control()
        panel.ContinueSessionButton = _Control()
        panel.NewSessionButton = _Control()
        panel.SessionState = _Control()
        panel.ConnectionState = _Control()
        panel.ConnectionDetail = _Control()

        panel._session_opened(
            {
                "active_session_id": None,
                "context_id": "context-a",
                "data_root": "C:\\test\\AI_Area_Assistant_Data",
                "requires_user_choice": True,
                "sessions": [
                    {
                        "session_id": "session-a",
                        "status": "idle",
                        "updated_at": "2026-08-13T00:00:00+00:00",
                    }
                ],
            },
            4,
            "document-a",
        )

        self.assertIsNone(panel._session_id)
        self.assertFalse(panel.SendButton.IsEnabled)
        self.assertTrue(panel.ContinueSessionButton.IsEnabled)
        self.assertTrue(panel.NewSessionButton.IsEnabled)
        self.assertEqual(panel._client.calls, [])
        self.assertIn("不会恢复、写记录或重放", panel.ConnectionDetail.Text)

    def test_continue_button_activates_only_the_current_document_session(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._client = _SessionClient()
        panel._dispatch = lambda callback: callback()
        panel._run_background = lambda callback: callback()
        panel._session_request_version = 2
        panel._panel_instance_id = "panel-a"
        panel._session_document_fingerprint = "document-a"
        panel._session_project_directory = "C:\\test"
        panel._pending_session_id = "session-a"
        panel._session_id = None
        panel._session_context_id = "context-a"
        panel._data_root = None
        panel.SendButton = _Control()
        panel.RetryButton = _Control()
        panel.ContinueSessionButton = _Control()
        panel.NewSessionButton = _Control()
        panel.SessionState = _Control()
        panel.ConnectionState = _Control()
        panel.ConnectionDetail = _Control()

        panel.continue_session_click(None, None)

        self.assertEqual(panel._session_id, "session-a")
        self.assertTrue(panel.SendButton.IsEnabled)
        self.assertEqual(
            panel._client.calls,
            [("choose", "document-a", "continue", "session-a")],
        )
        self.assertIn("等待你的新操作", panel.SessionState.Text)

    def test_document_switch_immediately_revokes_old_session_before_verification(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._bound_document_fingerprint = "document-a"
        panel._session_document_fingerprint = "document-a"
        panel._session_project_directory = "C:\\a"
        panel._session_request_version = 7
        panel._panel_instance_id = "panel-a"
        panel._client = _SessionClient()
        panel._session_id = "session-a"
        panel._session_context_id = "context-a"
        panel._pending_session_id = None
        panel._data_root = "C:\\a\\AI_Area_Assistant_Data"
        panel._document_pause_reason = None
        panel._run_background = lambda callback: callback()
        panel.SendButton = _Control()
        panel.SendButton.IsEnabled = True
        panel.ContinueSessionButton = _Control()
        panel.NewSessionButton = _Control()
        panel.SessionState = _Control()
        snapshot_b = {
            "document_fingerprint": "document-b",
            "document_path": "C:\\b\\model.rvt",
        }
        panel_module.collect_document_status = lambda document, path: snapshot_b
        observed = []
        panel._start_document_snapshot_verification = lambda snapshot: observed.append(
            (snapshot, panel._session_id, panel.SendButton.IsEnabled)
        )
        event_args = types.SimpleNamespace(
            CurrentActiveView=types.SimpleNamespace(Document=object())
        )

        panel._on_view_activated(None, event_args)

        self.assertEqual(observed, [(snapshot_b, None, False)])
        self.assertEqual(panel._document_pause_reason, "document_changed")
        self.assertIsNone(panel._session_document_fingerprint)
        self.assertIn("旧会话已暂停", panel.SessionState.Text)
        self.assertEqual(
            panel._client.calls,
            [("revoke", "panel-a", 8, "context-a")],
        )

    def test_document_switch_invalidates_local_session_before_agent_revoke_returns(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._session_request_version = 3
        panel._panel_instance_id = "panel-a"
        panel._session_id = "session-a"
        panel._session_context_id = "context-a"
        panel._pending_session_id = None
        panel._session_project_directory = "C:\\a"
        panel._session_document_fingerprint = "document-a"
        panel._data_root = "C:\\a\\AI_Area_Assistant_Data"
        panel.SendButton = _Control()
        panel.SendButton.IsEnabled = True
        panel.ContinueSessionButton = _Control()
        panel.NewSessionButton = _Control()
        panel.SessionState = _Control()
        panel._client = _ObservingRevokeClient(panel)
        callbacks = []
        panel._run_background = callbacks.append

        panel._pause_session_for_document_change()

        self.assertIsNone(panel._client.observed)
        self.assertIsNone(panel._session_id)
        self.assertIsNone(panel._session_context_id)
        self.assertFalse(panel.SendButton.IsEnabled)
        self.assertEqual(panel._session_request_version, 4)
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assertEqual(panel._client.observed, (None, None, False, 4))

    def test_late_reply_cannot_write_after_document_switch(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._dispatch = lambda callback: callback()
        panel._session_request_version = 1
        panel._panel_instance_id = "panel-a"
        panel._session_project_directory = "C:\\a"
        panel._session_document_fingerprint = "document-a"
        panel._session_id = "session-a"
        panel._session_context_id = "context-a"
        panel._pending_session_id = None
        panel._data_root = None
        panel.SendButton = _Control()
        panel.ContinueSessionButton = _Control()
        panel.NewSessionButton = _Control()
        panel.SessionState = _Control()
        panel.Transcript = _Control()
        panel.ConnectionState = _Control()
        panel.ConnectionDetail = _Control()
        panel.RetryButton = _Control()
        panel._client = _SwitchingClient(panel)
        context = panel._session_context()

        panel._stream("message for A", context)

        self.assertEqual(
            panel._client.records,
            [
                ("document-a", "session-a", "user", "message for A"),
                ("revoked", "context-a", 2),
            ],
        )
        self.assertNotIn("stale reply", panel.Transcript.Text)

    def test_stale_document_verification_cannot_overwrite_newer_result(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._document_request_version = 3
        panel.DocumentState = _Control()
        panel.DocumentState.Text = "bound"
        panel.DocumentDetail = _Control()
        panel.RefreshDocumentButton = _Control()

        panel._document_failed("old request failed", 2)

        self.assertEqual(panel.DocumentState.Text, "bound")


if __name__ == "__main__":
    unittest.main()
