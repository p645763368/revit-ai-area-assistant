import importlib
import sys
import threading
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


class _PlanClient(_SessionClient):
    def create_plan(
        self, project, fingerprint, context, panel, generation, session, message
    ):
        self.calls.append(("plan", fingerprint, session, message))
        return {
            "summary": "已比较候选来源。",
            "question": "采用哪个来源？",
            "options": [
                {"id": "floor", "label": "楼板", "recommended": True, "rationale": "轮廓完整", "impact": "继续精确核对"},
                {"id": "wall", "label": "墙体", "recommended": False, "rationale": "可交叉验证", "impact": "检查墙连接"},
            ],
        }


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


class _BlockingRevokeClient:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def revoke_session(self, panel_instance_id, generation, context_id):
        self.started.set()
        self.release.wait(2)
        return {"revoked": True}


class _FailOnceRevokeClient:
    def __init__(self, error_type):
        self.error_type = error_type
        self.calls = 0

    def revoke_session(self, panel_instance_id, generation, context_id):
        self.calls += 1
        if self.calls == 1:
            raise self.error_type("revoke unavailable")
        return {"revoked": True}


class _SwitchBetweenChecksEvent(dict):
    def __init__(self, panel):
        dict.__init__(
            self,
            message_type="response",
            status="completed",
            payload={"message": "stale reply"},
        )
        self.panel = panel
        self.payload_reads = 0

    def get(self, key, default=None):
        if key == "payload":
            self.payload_reads += 1
            if self.payload_reads == 2:
                self.panel._pause_session_for_document_change()
        return dict.get(self, key, default)


class _CheckThenSwitchClient(_SwitchingClient):
    def stream_chat(self, message):
        yield _SwitchBetweenChecksEvent(self.panel)


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
    fake_selection = types.ModuleType("area_assistant_pyrevit.selection")
    fake_selection.SelectionCancelled = type("SelectionCancelled", (Exception,), {})
    fake_selection.create_selection_executor = lambda callback: None
    fake_selection.current_selection = lambda: ([], 0)
    fake_selection.interactive_selection = lambda: []
    fake_selection.highlight_elements = lambda elements: None
    fake_selection.summarize_elements = lambda elements: [
        {
            "element_id": str(item),
            "category": "Floors",
            "level_name": "Level 1",
            "type_name": "Generic",
            "bounding_box_mm": [1000, 1000, 300],
        }
        for item in elements
    ]
    fake_selection.format_selection_summary = lambda summaries: "已选择 {} 个来源元素".format(
        len(summaries)
    )
    fake_process = types.ModuleType("area_assistant_pyrevit.process")
    fake_process.start_agent_process = lambda repository_root: None
    modules = {
        "Autodesk": fake_autodesk,
        "Autodesk.Revit": fake_revit,
        "Autodesk.Revit.UI": fake_ui,
        "System": fake_system,
        "pyrevit": fake_pyrevit,
        "area_assistant_pyrevit.process": fake_process,
        "area_assistant_pyrevit.selection": fake_selection,
    }
    sys.modules.pop("area_assistant_pyrevit.panel", None)
    with patch.dict(sys.modules, modules):
        return importlib.import_module("area_assistant_pyrevit.panel")


class PyRevitPanelTests(unittest.TestCase):
    def test_clickable_recommendation_continues_planning_in_current_session(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(panel_module.AiAreaAssistantPanel)
        panel._client = _PlanClient()
        panel._dispatch = lambda callback: callback()
        panel._run_background = lambda callback: callback()
        panel._session_request_version = 1
        panel._panel_instance_id = "panel-a"
        panel._session_project_directory = "C:\\test"
        panel._session_document_fingerprint = "document-a"
        panel._session_context_id = "context-a"
        panel._session_id = "session-a"
        panel._planning_active = True
        panel._planning_options = []
        for name in (
            "Transcript", "SendButton", "RetryButton", "AnalyzeButton",
            "ConnectionState", "ConnectionDetail", "StructuredSummary",
            "StructuredQuestion", "Option1Button", "Option2Button",
            "Option3Button", "Option4Button",
        ):
            setattr(panel, name, _Control())

        panel._request_plan("扫描当前模型")
        panel.option_1_click(None, None)

        self.assertEqual(panel.StructuredQuestion.Text, "采用哪个来源？")
        self.assertIn("★ 楼板", panel.Option1Button.Content)
        self.assertEqual(
            [call[3] for call in panel._client.calls if call[0] == "plan"],
            ["扫描当前模型", "选择方案：楼板（floor）"],
        )
        self.assertIn("依据：轮廓完整", panel.Transcript.Text)

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
        panel._dispatch = lambda callback: callback()
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
        self.assertFalse(panel.NewSessionButton.IsEnabled)
        panel.new_session_click(None, None)
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
        panel._dispatch = lambda callback: callback()
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

    def test_view_switch_returns_before_revoke_and_verifies_after_barrier(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._bound_document_fingerprint = "document-a"
        panel._session_document_fingerprint = "document-a"
        panel._session_project_directory = "C:\\a"
        panel._session_request_version = 7
        panel._panel_instance_id = "panel-a"
        panel._session_id = "session-a"
        panel._session_context_id = "context-a"
        panel._pending_session_id = None
        panel._data_root = None
        panel._document_pause_reason = None
        panel.SendButton = _Control()
        panel.SendButton.IsEnabled = True
        panel.ContinueSessionButton = _Control()
        panel.NewSessionButton = _Control()
        panel.SessionState = _Control()
        panel._client = _BlockingRevokeClient()
        threads = []

        def run_background(callback):
            thread = threading.Thread(target=callback)
            thread.start()
            threads.append(thread)

        panel._run_background = run_background
        panel._dispatch = lambda callback: callback()
        snapshot_b = {
            "document_fingerprint": "document-b",
            "document_path": "C:\\b\\model.rvt",
        }
        panel_module.collect_document_status = lambda document, path: snapshot_b
        observed = []
        panel._start_document_snapshot_verification = observed.append
        event_args = types.SimpleNamespace(
            CurrentActiveView=types.SimpleNamespace(Document=object())
        )

        panel._on_view_activated(None, event_args)

        self.assertTrue(panel._client.started.wait(1))
        self.assertEqual(observed, [])
        self.assertIsNone(panel._session_id)
        self.assertFalse(panel.SendButton.IsEnabled)
        panel._client.release.set()
        threads[0].join(2)
        self.assertFalse(threads[0].is_alive())
        self.assertEqual(observed, [snapshot_b])

    def test_failed_revoke_stays_paused_until_explicit_retry_succeeds(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(
            panel_module.AiAreaAssistantPanel
        )
        panel._bound_document_fingerprint = "document-a"
        panel._session_document_fingerprint = "document-a"
        panel._session_project_directory = "C:\\a"
        panel._session_request_version = 7
        panel._panel_instance_id = "panel-a"
        panel._session_id = "session-a"
        panel._session_context_id = "context-a"
        panel._pending_session_id = None
        panel._pending_session_revoke = None
        panel._data_root = None
        panel._document_pause_reason = None
        panel.SendButton = _Control()
        panel.SendButton.IsEnabled = True
        panel.ContinueSessionButton = _Control()
        panel.NewSessionButton = _Control()
        panel.RefreshDocumentButton = _Control()
        panel.SessionState = _Control()
        panel.ConnectionState = _Control()
        panel.ConnectionDetail = _Control()
        panel._client = _FailOnceRevokeClient(panel_module.AgentConnectionError)
        panel._run_background = lambda callback: callback()
        panel._dispatch = lambda callback: callback()
        snapshot_b = {
            "document_fingerprint": "document-b",
            "document_path": "C:\\b\\model.rvt",
        }
        panel_module.collect_document_status = lambda document, path: snapshot_b
        observed = []
        panel._start_document_snapshot_verification = observed.append
        event_args = types.SimpleNamespace(
            CurrentActiveView=types.SimpleNamespace(Document=object())
        )

        panel._on_view_activated(None, event_args)

        self.assertEqual(observed, [])
        self.assertEqual(panel._client.calls, 1)
        self.assertFalse(panel.SendButton.IsEnabled)
        self.assertFalse(panel.ContinueSessionButton.IsEnabled)
        self.assertFalse(panel.NewSessionButton.IsEnabled)
        self.assertTrue(panel.RefreshDocumentButton.IsEnabled)
        self.assertIn("撤销失败", panel.SessionState.Text)
        self.assertIn("不会验证或打开", panel.ConnectionDetail.Text)
        panel._on_view_activated(None, event_args)
        self.assertEqual(panel._client.calls, 1)
        self.assertEqual(observed, [])

        panel.refresh_document_click(None, None)

        self.assertEqual(panel._client.calls, 2)
        self.assertEqual(observed, [snapshot_b])
        self.assertIsNone(panel._pending_session_revoke)

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
        panel._pause_session_for_document_change()

        self.assertIsNone(panel._session_id)
        self.assertIsNone(panel._session_context_id)
        self.assertFalse(panel.SendButton.IsEnabled)
        self.assertEqual(panel._session_request_version, 4)
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

    def test_switch_after_completed_check_still_skips_assistant_record(self):
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
        panel._client = _CheckThenSwitchClient(panel)
        context = panel._session_context()

        panel._stream("message for A", context)

        self.assertEqual(
            panel._client.records,
            [
                ("document-a", "session-a", "user", "message for A"),
                ("revoked", "context-a", 2),
            ],
        )

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

    def test_current_selection_updates_panel_and_enables_actions(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(panel_module.AiAreaAssistantPanel)
        panel_module.current_selection = lambda: ([101, 102], 0)
        panel._selected_elements = []
        panel._selected_summaries = []
        panel._selection_document = None
        panel.SelectionState = _Control()
        panel.SelectionDetail = _Control()
        panel.HighlightSelectionButton = _Control()
        panel.AnalyzeSelectionButton = _Control()
        panel._selection_executor = types.SimpleNamespace(
            request=lambda action, elements=None: panel._selection_event_completed(
                "selected",
                [
                    types.SimpleNamespace(Document=types.SimpleNamespace()),
                    types.SimpleNamespace(Document=types.SimpleNamespace()),
                ],
                0,
                None,
            )
        )

        panel.read_selection_click(None, None)

        self.assertEqual(panel.SelectionState.Text, "已选择 2 个")
        self.assertIn("已选择 2 个来源元素", panel.SelectionDetail.Text)
        self.assertTrue(panel.HighlightSelectionButton.IsEnabled)
        self.assertTrue(panel.AnalyzeSelectionButton.IsEnabled)

    def test_interactive_cancel_is_visible_and_keeps_previous_selection(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(panel_module.AiAreaAssistantPanel)

        panel._selected_elements = [101]
        panel._selected_summaries = [{"element_id": "101"}]
        panel.SelectionState = _Control()
        panel.SelectionDetail = _Control()
        panel._selection_executor = types.SimpleNamespace(
            request=lambda action, elements=None: panel._selection_event_completed(
                "cancelled", [], 0, None
            )
        )

        panel.pick_selection_click(None, None)

        self.assertEqual(panel.SelectionState.Text, "已取消选择")
        self.assertEqual(panel._selected_elements, [101])

    def test_selected_elements_can_be_sent_to_agent_through_existing_chat(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(panel_module.AiAreaAssistantPanel)
        panel._selected_summaries = [{"element_id": "101"}]
        sent = []
        panel._send = sent.append

        panel.analyze_selection_click(None, None)

        self.assertEqual(len(sent), 1)
        self.assertIn("已选择 1 个来源元素", sent[0])

    def test_document_switch_clears_selection_before_initial_binding_completes(self):
        panel_module = _load_panel_module()
        panel = panel_module.AiAreaAssistantPanel.__new__(panel_module.AiAreaAssistantPanel)

        class _Document:
            def __init__(self, name):
                self.name = name

            def Equals(self, other):
                return self is other

        old_document = _Document("old")
        new_document = _Document("new")
        panel._selection_document = old_document
        panel._selected_elements = [types.SimpleNamespace(Document=old_document)]
        panel._selected_summaries = [{"element_id": "101"}]
        panel._bound_document_fingerprint = None
        panel._document_pause_reason = None
        panel.HighlightSelectionButton = _Control()
        panel.HighlightSelectionButton.IsEnabled = True
        panel.AnalyzeSelectionButton = _Control()
        panel.AnalyzeSelectionButton.IsEnabled = True
        panel.SelectionState = _Control()
        panel.SelectionDetail = _Control()
        panel._start_document_snapshot_verification = lambda snapshot: None
        panel_module.collect_document_status = lambda document, path: {
            "document_fingerprint": document.name,
        }
        event_args = types.SimpleNamespace(
            CurrentActiveView=types.SimpleNamespace(Document=new_document)
        )

        panel._on_view_activated(None, event_args)

        self.assertEqual(panel._selected_elements, [])
        self.assertEqual(panel._selected_summaries, [])
        self.assertIsNone(panel._selection_document)
        self.assertFalse(panel.AnalyzeSelectionButton.IsEnabled)
        self.assertEqual(panel.SelectionState.Text, "未选择")


if __name__ == "__main__":
    unittest.main()
