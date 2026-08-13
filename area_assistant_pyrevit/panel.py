# -*- coding: utf-8 -*-
"""WPF dockable pane hosted by pyRevit's IronPython Forms backend."""

from __future__ import unicode_literals

import os
import threading

import Autodesk.Revit.UI as UI
from System import Action
from pyrevit import HOST_APP, forms, framework, revit

from . import PANEL_ID
from .client import AgentClient, AgentConnectionError, ensure_agent_available
from .document_status import collect_document_status
from .process import start_agent_process
from .selection import create_selection_executor, format_selection_summary, summarize_elements


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
        self._selected_elements = []
        self._selected_summaries = []
        self._selection_executor = create_selection_executor(
            self._selection_event_completed
        )
        self.SendButton.Click += self.send_click
        self.RetryButton.Click += self.retry_click
        self.RefreshDocumentButton.Click += self.refresh_document_click
        self.ReadSelectionButton.Click += self.read_selection_click
        self.PickSelectionButton.Click += self.pick_selection_click
        self.HighlightSelectionButton.Click += self.highlight_selection_click
        self.AnalyzeSelectionButton.Click += self.analyze_selection_click
        self._subscribe_document_changes()
        self._set_busy(True)
        self._set_status("连接中", "正在连接本地 Agent…")
        self._set_document_status("待验证", "打开文档后点击“验证文档”")
        self._set_selection_status("未选择", "请选择Floor、Roof或Wall作为边界来源")
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
            self._clear_selection("文档已切换，请重新选择来源元素")
        self._start_document_snapshot_verification(snapshot)

    def read_selection_click(self, sender, args):
        self._set_selection_status("读取中", "正在读取Revit当前选择集…")
        self._request_selection_operation("current")

    def pick_selection_click(self, sender, args):
        self._set_selection_status("选择中", "请在Revit中选择Floor、Roof或Wall")
        self._request_selection_operation("interactive")

    def highlight_selection_click(self, sender, args):
        if not self._selected_elements:
            self._set_selection_status("未选择", "请先读取或交互选择来源元素")
            return
        self._set_selection_status("定位中", "正在Revit中定位所选元素…")
        self._request_selection_operation("highlight", self._selected_elements)

    def _request_selection_operation(self, action, elements=None):
        try:
            self._selection_executor.request(action, elements)
        except Exception as exc:
            self._set_selection_status("操作失败", str(exc))

    def _selection_event_completed(self, outcome, elements, rejected_count, error):
        if outcome == "selected":
            self._update_selection(elements, rejected_count)
        elif outcome == "highlighted":
            self._set_selection_status(
                "已定位 {} 个".format(len(elements)),
                format_selection_summary(self._selected_summaries),
            )
        elif outcome == "cancelled":
            self._set_selection_status("已取消选择", "保留上一次有效选择")
        else:
            self._set_selection_status("操作失败", error or "未知选择错误")

    def analyze_selection_click(self, sender, args):
        if not self._selected_summaries:
            self._set_selection_status("未选择", "请先读取或交互选择来源元素")
            return
        self._send(
            "请分析以下Revit边界来源元素。不要执行模型写入；如证据不足，请先提问。\n{}".format(
                format_selection_summary(self._selected_summaries)
            )
        )

    def _update_selection(self, elements, rejected_count):
        self._selected_elements = list(elements)
        self._selected_summaries = summarize_elements(self._selected_elements)
        count = len(self._selected_elements)
        self.HighlightSelectionButton.IsEnabled = count > 0
        self.AnalyzeSelectionButton.IsEnabled = count > 0
        if count == 0:
            detail = "当前选择中没有Floor、Roof或Wall"
        else:
            detail = format_selection_summary(self._selected_summaries)
        if rejected_count:
            detail += "\n已忽略 {} 个不支持类别的元素".format(rejected_count)
        self._set_selection_status("已选择 {} 个".format(count), detail)

    def _clear_selection(self, detail):
        self._selected_elements = []
        self._selected_summaries = []
        self.HighlightSelectionButton.IsEnabled = False
        self.AnalyzeSelectionButton.IsEnabled = False
        self._set_selection_status("未选择", detail)

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
        self._last_message = message
        self.MessageInput.Text = ""
        self.Transcript.AppendText("你：{}\nAI：".format(message))
        self._set_busy(True)
        self.RetryButton.IsEnabled = False
        self._set_status("回答中", "正在接收流式回复…")
        self._run_background(lambda: self._stream(message))

    def _stream(self, message):
        try:
            for event in self._client.stream_chat(message):
                if event.get("message_type") == "response":
                    delta = event.get("payload", {}).get("delta")
                    if delta:
                        self._dispatch(lambda text=delta: self.Transcript.AppendText(text))
                    if event.get("status") == "completed":
                        self._dispatch(self._reply_completed)
                elif event.get("message_type") == "error":
                    self._dispatch(
                        lambda item=event: self._reply_failed(
                            item.get("message", "模型 API 请求失败。"),
                            item.get("retryable", False),
                        )
                    )
        except AgentConnectionError as exc:
            message = str(exc)
            self._dispatch(lambda text=message: self._reply_failed(text, True))

    def _reply_completed(self):
        self.Transcript.AppendText("\n\n")
        self._set_busy(False)
        self._set_status("已连接", "回复完成")

    def _reply_failed(self, message, retryable):
        self.Transcript.AppendText("\n[错误] {}\n\n".format(message))
        self._set_busy(False)
        self._set_status("请求失败", message)
        self.RetryButton.IsEnabled = bool(retryable)

    def _set_busy(self, busy):
        self.SendButton.IsEnabled = not busy
        if busy:
            self.RetryButton.IsEnabled = False

    def _set_status(self, state, detail):
        self.ConnectionState.Text = state
        self.ConnectionDetail.Text = detail

    def _set_document_status(self, state, detail):
        self.DocumentState.Text = state
        self.DocumentDetail.Text = detail

    def _set_selection_status(self, state, detail):
        self.SelectionState.Text = state
        self.SelectionDetail.Text = detail

    def _dispatch(self, callback):
        self.Dispatcher.BeginInvoke(Action(callback))

    @staticmethod
    def _run_background(callback):
        worker = threading.Thread(target=callback)
        worker.daemon = True
        worker.start()
