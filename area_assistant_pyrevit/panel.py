# -*- coding: utf-8 -*-
"""WPF dockable pane hosted by pyRevit's IronPython Forms backend."""

from __future__ import unicode_literals

import os
import threading
import uuid

import Autodesk.Revit.UI as UI
from System import Action
from pyrevit import HOST_APP, forms, framework, revit

from . import PANEL_ID
from .client import AgentClient, AgentConnectionError, ensure_agent_available
from .document_status import collect_document_status
from .process import start_agent_process


_PACKAGE_ROOT = os.path.dirname(__file__)
_REPOSITORY_ROOT = os.path.dirname(_PACKAGE_ROOT)


class AiAreaAssistantPanel(forms.WPFPanel):
    panel_id = PANEL_ID
    panel_source = os.path.join(_PACKAGE_ROOT, "panel.xaml")
    panel_title = "AI Area Assistant"
    initial_state = UI.DockablePaneState()
    initial_state.DockPosition = UI.DockPosition.Right

    def __init__(self):
        forms.WPFPanel.__init__(self)
        port = os.environ.get("AI_AREA_ASSISTANT_PORT", "8765")
        self._client = AgentClient("http://127.0.0.1:{}".format(port), timeout_seconds=50)
        self._last_message = None
        self._document_pause_reason = None
        self._bound_document_fingerprint = None
        self._document_request_version = 0
        self._session_request_version = 0
        self._panel_instance_id = uuid.uuid4().hex
        self._session_id = None
        self._session_context_id = None
        self._pending_session_id = None
        self._session_project_directory = None
        self._session_document_fingerprint = None
        self._data_root = None
        self.SendButton.Click += self.send_click
        self.RetryButton.Click += self.retry_click
        self.RefreshDocumentButton.Click += self.refresh_document_click
        self.ContinueSessionButton.Click += self.continue_session_click
        self.NewSessionButton.Click += self.new_session_click
        self._subscribe_document_changes()
        self._set_busy(True)
        self._set_status("连接中", "正在连接本地 Agent…")
        self._set_document_status("待验证", "打开文档后点击“验证文档”")
        self._set_session_status("等待文档验证")
        self._run_background(self._connect)

    def _connect(self):
        try:
            available = ensure_agent_available(
                self._client,
                lambda: start_agent_process(_REPOSITORY_ROOT),
            )
        except Exception as exc:
            message = str(exc)
            self._dispatch(lambda text=message: self._connection_failed(text))
            return
        if available:
            self._dispatch(self._connection_ready)
        else:
            self._dispatch(lambda: self._connection_failed("本地 Agent 启动超时。"))

    def _connection_ready(self):
        self._set_busy(False)
        self._set_status("已连接", "本地 Agent 已就绪")
        self.refresh_document_click(None, None)

    def _connection_failed(self, message):
        self._set_busy(False)
        self._set_status("连接失败", message)
        self.RetryButton.IsEnabled = True

    def send_click(self, sender, args):
        message = self.MessageInput.Text.strip()
        if message:
            self._send(message)

    def continue_session_click(self, sender, args):
        if self._pending_session_id:
            self._choose_session("continue", self._pending_session_id)

    def new_session_click(self, sender, args):
        if self._session_document_fingerprint:
            self._choose_session("new", None)

    def retry_click(self, sender, args):
        if self._last_message:
            self._send(self._last_message)
        else:
            self._set_busy(True)
            self._set_status("连接中", "正在重试本地 Agent…")
            self._run_background(self._connect)

    def refresh_document_click(self, sender, args):
        document = getattr(revit, "doc", None)
        if document is None:
            self._set_document_status("无活动文档", "请先打开Revit文档")
            return
        self._start_document_verification(document)

    def _start_document_verification(self, document):
        snapshot = collect_document_status(
            document,
            os.environ.get("AI_AREA_ASSISTANT_TEST_DOCUMENT", ""),
        )
        self._start_document_snapshot_verification(snapshot)

    def _start_document_snapshot_verification(self, snapshot):
        self._document_request_version += 1
        request_version = self._document_request_version
        self._set_document_status("验证中", self._document_summary(snapshot, None))
        self.RefreshDocumentButton.IsEnabled = False
        self._run_background(lambda: self._verify_document(snapshot, request_version))

    def _verify_document(self, snapshot, request_version):
        try:
            response = self._client.document_status(
                snapshot,
                pause_reason=self._document_pause_reason,
            )
            if response.get("message_type") == "error":
                raise AgentConnectionError(
                    response.get("message", "文档安全验证失败。")
                )
            self._dispatch(
                lambda: self._document_verified(
                    snapshot, response["payload"], request_version
                )
            )
        except AgentConnectionError as exc:
            message = str(exc)
            self._dispatch(
                lambda text=message: self._document_failed(text, request_version)
            )

    def _document_verified(self, snapshot, binding, request_version):
        if request_version != self._document_request_version:
            return
        self._document_pause_reason = binding.get("pause_reason")
        if binding.get("binding_status") == "bound":
            self._bound_document_fingerprint = snapshot["document_fingerprint"]
        self._set_document_status(
            binding.get("binding_status", "unavailable"),
            self._document_summary(snapshot, binding),
        )
        self.RefreshDocumentButton.IsEnabled = True
        self._start_session_for_snapshot(snapshot)

    def _document_failed(self, message, request_version):
        if request_version != self._document_request_version:
            return
        self._set_document_status("验证失败", message)
        self.RefreshDocumentButton.IsEnabled = True

    def _on_view_activated(self, sender, event_args):
        snapshot = collect_document_status(
            event_args.CurrentActiveView.Document,
            os.environ.get("AI_AREA_ASSISTANT_TEST_DOCUMENT", ""),
        )
        if (
            self._bound_document_fingerprint is not None
            and snapshot["document_fingerprint"] != self._bound_document_fingerprint
        ):
            self._document_pause_reason = "document_changed"
        if (
            self._session_document_fingerprint is not None
            and snapshot["document_fingerprint"]
            != self._session_document_fingerprint
        ):
            self._pause_session_for_document_change()
        self._start_document_snapshot_verification(snapshot)

    def _subscribe_document_changes(self):
        try:
            self._view_handler = framework.EventHandler[UI.Events.ViewActivatedEventArgs](
                self._on_view_activated
            )
            HOST_APP.uiapp.ViewActivated += self._view_handler
        except Exception:
            self._view_handler = None

    @staticmethod
    def _document_summary(snapshot, binding):
        view = snapshot["active_view"]
        lines = [
            "实例：{}".format(snapshot["revit_instance_id"]),
            "路径：{}".format(snapshot["document_path"] or "<unsaved>"),
            "视图：{} ({})".format(view["name"], view["id"]),
            "IsModified：{}".format(snapshot["is_modified"]),
            "授权路径：{}".format("yes" if snapshot["authorized_path_match"] else "no"),
        ]
        if binding is not None:
            lines.extend(
                [
                    "rvt-mcp：{}".format(binding["rvt_mcp_status"]),
                    "写入许可：{}".format(
                        "allowed" if binding["write_allowed"] else "denied"
                    ),
                    "暂停原因：{}".format(binding["pause_reason"] or "none"),
                ]
            )
        return "\n".join(lines)

    def _send(self, message):
        context = self._session_context()
        if context is None:
            self._set_status("等待选择", "请先为当前文档选择继续或新建会话")
            return
        self._last_message = message
        self.MessageInput.Text = ""
        self.Transcript.AppendText("你：{}\nAI：".format(message))
        self._set_busy(True)
        self.RetryButton.IsEnabled = False
        self._set_status("回答中", "正在接收流式回复…")
        self._run_background(lambda: self._stream(message, context))

    def _stream(self, message, context=None):
        context = context or self._session_context()
        if context is None or not self._session_is_current(context):
            return
        try:
            self._client.record_message(
                context[1],
                context[2],
                context[3],
                self._panel_instance_id,
                context[0],
                context[4],
                "user",
                message,
            )
            complete = []
            for event in self._client.stream_chat(message):
                if not self._session_is_current(context):
                    return
                if event.get("message_type") == "response":
                    delta = event.get("payload", {}).get("delta")
                    if delta:
                        complete.append(delta)
                        self._dispatch(lambda text=delta: self.Transcript.AppendText(text))
                    if event.get("status") == "completed":
                        assistant_message = event.get("payload", {}).get(
                            "message", "".join(complete)
                        )
                        self._client.record_message(
                            context[1],
                            context[2],
                            context[3],
                            self._panel_instance_id,
                            context[0],
                            context[4],
                            "assistant",
                            assistant_message,
                        )
                        self._dispatch(
                            lambda item=context: self._reply_completed(item)
                        )
                elif event.get("message_type") == "error":
                    self._dispatch(
                        lambda item=event, session=context: self._reply_failed(
                            item.get("message", "模型 API 请求失败。"),
                            item.get("retryable", False),
                            session,
                        )
                    )
        except AgentConnectionError as exc:
            message = str(exc)
            self._dispatch(
                lambda text=message, session=context: self._reply_failed(
                    text, True, session
                )
            )

    def _reply_completed(self, context=None):
        if context is not None and not self._session_is_current(context):
            return
        self.Transcript.AppendText("\n\n")
        self._set_busy(False)
        self._set_status("已连接", "回复完成")

    def _reply_failed(self, message, retryable, context=None):
        if context is not None and not self._session_is_current(context):
            return
        self.Transcript.AppendText("\n[错误] {}\n\n".format(message))
        self._set_busy(False)
        self._set_status("请求失败", message)
        self.RetryButton.IsEnabled = bool(retryable)

    def _set_busy(self, busy):
        session_ready = getattr(self, "_session_id", "legacy") is not None
        self.SendButton.IsEnabled = not busy and session_ready
        if busy:
            self.RetryButton.IsEnabled = False

    def _set_status(self, state, detail):
        self.ConnectionState.Text = state
        self.ConnectionDetail.Text = detail

    def _set_document_status(self, state, detail):
        self.DocumentState.Text = state
        self.DocumentDetail.Text = detail

    def _start_session_for_snapshot(self, snapshot):
        fingerprint = snapshot.get("document_fingerprint")
        document_path = snapshot.get("document_path")
        if not fingerprint or not document_path:
            self._pause_session_for_document_change()
            self._set_session_status("请先保存当前Revit文档")
            return
        if (
            fingerprint == self._session_document_fingerprint
            and (self._session_id is not None or self._session_context_id is not None)
        ):
            return
        self._session_request_version += 1
        request_version = self._session_request_version
        self._session_id = None
        self._session_context_id = None
        self._pending_session_id = None
        self._session_document_fingerprint = fingerprint
        self._session_project_directory = os.path.dirname(document_path)
        self.ContinueSessionButton.IsEnabled = False
        self.NewSessionButton.IsEnabled = False
        self.SendButton.IsEnabled = False
        self._set_session_status("正在读取当前文档的会话…")

        def open_current_session():
            try:
                result = self._client.open_session(
                    self._session_project_directory,
                    fingerprint,
                    self._panel_instance_id,
                    request_version,
                )
            except AgentConnectionError as exc:
                message = str(exc)
                self._dispatch(
                    lambda text=message, version=request_version: self._session_failed(
                        text, version
                    )
                )
                return
            self._dispatch(
                lambda payload=result, version=request_version, current=fingerprint: self._session_opened(
                    payload, version, current
                )
            )

        self._run_background(open_current_session)

    def _session_opened(self, result, request_version, fingerprint):
        if (
            request_version != self._session_request_version
            or fingerprint != self._session_document_fingerprint
        ):
            return
        sessions = result.get("sessions", [])
        self._data_root = result.get("data_root")
        self._session_context_id = result.get("context_id")
        self._pending_session_id = (
            sessions[0].get("session_id") if sessions else None
        )
        if self._pending_session_id:
            detail = "发现上次会话，请选择继续或新建会话。"
            self.ContinueSessionButton.IsEnabled = True
        else:
            detail = "当前文档没有上次会话，请选择新建会话。"
            self.ContinueSessionButton.IsEnabled = False
        self.NewSessionButton.IsEnabled = True
        self.SendButton.IsEnabled = False
        self._set_session_status(self._session_detail(detail))
        self._set_status("等待选择", "选择前不会恢复、写记录或重放旧操作")

    def _choose_session(self, choice, session_id):
        context = (
            self._session_request_version,
            self._session_project_directory,
            self._session_document_fingerprint,
            self._session_context_id,
        )
        self._set_busy(True)
        self.ContinueSessionButton.IsEnabled = False
        self.NewSessionButton.IsEnabled = False
        self._set_status("会话处理中", "正在应用你的明确选择…")

        def choose():
            try:
                result = self._client.choose_session(
                    context[1],
                    context[2],
                    context[3],
                    self._panel_instance_id,
                    context[0],
                    choice,
                    session_id,
                )
            except AgentConnectionError as exc:
                message = str(exc)
                self._dispatch(
                    lambda text=message, version=context[0]: self._session_failed(
                        text, version
                    )
                )
                return
            detail = (
                "已继续上次会话，等待你的新操作。"
                if choice == "continue"
                else "新会话已建立，等待你的操作。"
            )
            self._dispatch(
                lambda payload=result, text=detail, expected=context: self._activate_session(
                    payload, text, expected
                )
            )

        self._run_background(choose)

    def _activate_session(self, result, detail, expected_context):
        if (
            expected_context[0] != self._session_request_version
            or expected_context[2] != self._session_document_fingerprint
        ):
            return
        self._session_id = result.get("active_session_id")
        self._session_context_id = result.get("context_id")
        self._data_root = result.get("data_root") or self._data_root
        self._pending_session_id = None
        self.ContinueSessionButton.IsEnabled = False
        self.NewSessionButton.IsEnabled = True
        self._set_session_status(self._session_detail(detail))
        self._set_busy(False)
        self._set_status("已连接", "当前文档会话已就绪")

    def _pause_session_for_document_change(self):
        next_version = self._session_request_version + 1
        context_id = self._session_context_id
        self._session_request_version = next_version
        self._session_id = None
        self._session_context_id = None
        self._pending_session_id = None
        self._session_project_directory = None
        self._session_document_fingerprint = None
        self._data_root = None
        self.SendButton.IsEnabled = False
        self.ContinueSessionButton.IsEnabled = False
        self.NewSessionButton.IsEnabled = False
        self._set_session_status("文档已切换；旧会话已暂停，正在读取新文档")
        if context_id is not None:
            self._run_background(
                lambda: self._revoke_session(next_version, context_id)
            )

    def _revoke_session(self, generation, context_id):
        try:
            self._client.revoke_session(
                self._panel_instance_id, generation, context_id
            )
        except AgentConnectionError:
            pass

    def _session_failed(self, message, request_version):
        if request_version != self._session_request_version:
            return
        self._session_id = None
        self.SendButton.IsEnabled = False
        self.NewSessionButton.IsEnabled = True
        self._set_session_status("会话不可用：{}".format(message))
        self._set_status("会话失败", message)

    def _session_context(self):
        if (
            self._session_id is None
            or self._session_project_directory is None
            or self._session_document_fingerprint is None
            or self._session_context_id is None
        ):
            return None
        return (
            self._session_request_version,
            self._session_project_directory,
            self._session_document_fingerprint,
            self._session_context_id,
            self._session_id,
        )

    def _session_is_current(self, context):
        return context == self._session_context()

    def _session_detail(self, detail):
        if self._data_root:
            return "{}\n数据目录：{}".format(detail, self._data_root)
        return detail

    def _set_session_status(self, detail):
        self.SessionState.Text = detail

    def _dispatch(self, callback):
        self.Dispatcher.BeginInvoke(Action(callback))

    @staticmethod
    def _run_background(callback):
        worker = threading.Thread(target=callback)
        worker.daemon = True
        worker.start()
