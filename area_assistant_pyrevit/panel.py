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
from .client import (
    AgentClient,
    AgentConnectionError,
    PlanJobTransportError,
    ensure_agent_available,
)
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
        self._client = AgentClient(
            "http://127.0.0.1:{}".format(port),
            timeout_seconds=50,
            job_timeout_seconds=2.0,
        )
        self._last_message = None
        self._document_pause_reason = None
        self._bound_document_fingerprint = None
        self._document_request_version = 0
        self._selected_elements = []
        self._selected_summaries = []
        self._selection_document = None
        self._selection_executor = create_selection_executor(
            self._selection_event_completed
        )
        self._session_request_version = 0
        self._panel_instance_id = uuid.uuid4().hex
        self._session_id = None
        self._session_context_id = None
        self._pending_session_id = None
        self._session_project_directory = None
        self._session_document_fingerprint = None
        self._data_root = None
        self._pending_session_revoke = None
        self._planning_active = False
        self._planning_job_id = None
        self._planning_job_context = None
        self._planning_poll_generation = 0
        self._planning_terminal_retry_available = False
        self._planning_options = []
        self.SendButton.Click += self.send_click
        self.AnalyzeButton.Click += self.analyze_click
        self.Option1Button.Click += self.option_1_click
        self.Option2Button.Click += self.option_2_click
        self.Option3Button.Click += self.option_3_click
        self.Option4Button.Click += self.option_4_click
        self.RetryButton.Click += self.retry_click
        self.RefreshDocumentButton.Click += self.refresh_document_click
        self.ContinueSessionButton.Click += self.continue_session_click
        self.NewSessionButton.Click += self.new_session_click
        self.ReadSelectionButton.Click += self.read_selection_click
        self.PickSelectionButton.Click += self.pick_selection_click
        self.HighlightSelectionButton.Click += self.highlight_selection_click
        self.AnalyzeSelectionButton.Click += self.analyze_selection_click
        self._subscribe_document_changes()
        self._set_busy(True)
        self._set_status("连接中", "正在连接本地 Agent…")
        self._set_document_status("待验证", "打开文档后点击“验证文档”")
        self._set_selection_status("未选择", "请选择Floor、Roof或Wall作为边界来源")
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
            if self._planning_active:
                self._request_plan(message)
            else:
                self._send(message)

    def analyze_click(self, sender, args):
        self._planning_active = True
        self._request_plan(
            "请只读扫描当前Revit模型，按需截图并比较面积边界候选来源。"
        )

    def option_1_click(self, sender, args):
        self._select_plan_option(0)

    def option_2_click(self, sender, args):
        self._select_plan_option(1)

    def option_3_click(self, sender, args):
        self._select_plan_option(2)

    def option_4_click(self, sender, args):
        self._select_plan_option(3)

    def continue_session_click(self, sender, args):
        if self._pending_session_id:
            self._choose_session("continue", self._pending_session_id)

    def new_session_click(self, sender, args):
        if self.NewSessionButton.IsEnabled and self._session_document_fingerprint:
            self._choose_session("new", None)

    def retry_click(self, sender, args):
        if self._last_message:
            if self._planning_active:
                self._request_plan(
                    self._last_message,
                    retry_terminal=getattr(
                        self, "_planning_terminal_retry_available", False
                    ),
                )
            else:
                self._send(self._last_message)
        else:
            self._set_busy(True)
            self._set_status("连接中", "正在重试本地 Agent…")
            self._run_background(self._connect)

    def refresh_document_click(self, sender, args):
        if getattr(self, "_pending_session_revoke", None) is not None:
            generation, context_id, callback = self._pending_session_revoke
            self.RefreshDocumentButton.IsEnabled = False
            self._run_background(
                lambda: self._revoke_then_continue(
                    generation, context_id, callback
                )
            )
            return
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
        active_document = event_args.CurrentActiveView.Document
        snapshot = collect_document_status(
            active_document,
            os.environ.get("AI_AREA_ASSISTANT_TEST_DOCUMENT", ""),
        )
        if getattr(self, "_pending_session_revoke", None) is not None:
            return
        if (
            getattr(self, "_selection_document", None) is not None
            and not self._selection_document.Equals(active_document)
        ):
            self._clear_selection("文档已切换，请重新选择来源元素")
        if (
            self._bound_document_fingerprint is not None
            and snapshot["document_fingerprint"] != self._bound_document_fingerprint
        ):
            self._document_pause_reason = "document_changed"
        if (
            getattr(self, "_session_document_fingerprint", None) is not None
            and snapshot["document_fingerprint"]
            != self._session_document_fingerprint
        ):
            self._pause_session_for_document_change(
                lambda: self._start_document_snapshot_verification(snapshot)
            )
            return
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
        self._selection_document = (
            self._selected_elements[0].Document if self._selected_elements else None
        )
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
        self._selection_document = None
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
                        if not self._session_is_current(context):
                            return
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

    def _request_plan(self, message, retry_terminal=False):
        context = self._session_context()
        if context is None:
            self._set_status("等待选择", "请先为当前文档选择继续或新建会话")
            return
        if (
            getattr(self, "_planning_job_context", None) == context
            and getattr(self, "_planning_poll_generation", 0) > 0
        ):
            self._set_status(
                "等待 Agent 完成",
                "当前规划任务仍在运行；本会话已阻止重复提交。",
            )
            self.RetryButton.IsEnabled = False
            return
        self._planning_active = True
        self._last_message = message
        retry_terminal = bool(
            retry_terminal
            and getattr(self, "_planning_terminal_retry_available", False)
        )
        self._planning_terminal_retry_available = False
        self._planning_poll_generation = (
            getattr(self, "_planning_poll_generation", 0) + 1
        )
        poll_generation = self._planning_poll_generation
        self._planning_job_id = None
        self._planning_job_context = context
        if hasattr(self, "MessageInput"):
            self.MessageInput.Text = ""
        self.Transcript.AppendText("你：{}\n".format(message))
        self._set_busy(True)
        self._set_plan_options_enabled(False)
        self._set_status("分析中", "AI正在只读扫描模型并比较证据…")
        self._run_background(
            lambda: self._submit_plan_job(
                message, context, poll_generation, retry_terminal
            )
        )

    def _submit_plan_job(
        self, message, context, poll_generation, retry_terminal=False
    ):
        attempt_retry_terminal = retry_terminal
        while self._planning_poll_is_current(context, poll_generation):
            try:
                snapshot = self._client.submit_plan_job(
                    context[1],
                    context[2],
                    context[3],
                    self._panel_instance_id,
                    context[0],
                    context[4],
                    message,
                    attempt_retry_terminal,
                )
                break
            except PlanJobTransportError:
                attempt_retry_terminal = False
                self._dispatch(
                    lambda session=context, generation=poll_generation: self._planning_submit_connection_failed(
                        session, generation
                    )
                )
                if not self._planning_poll_is_current(
                    context, poll_generation
                ):
                    return
                threading.Event().wait(1.0)
            except AgentConnectionError as exc:
                error_message = str(exc)
                self._dispatch(
                    lambda text=error_message, session=context, generation=poll_generation: self._planning_submit_failed(
                        text, session, generation
                    )
                )
                return
        else:
            return
        if not self._planning_poll_is_current(context, poll_generation):
            return
        job_id = snapshot["job_id"]
        self._planning_job_id = job_id
        self._dispatch(
            lambda payload=snapshot, session=context, generation=poll_generation: self._apply_plan_job(
                payload, session, generation
            )
        )
        if snapshot["state"] in ("queued", "running"):
            self._poll_plan_job(job_id, context, poll_generation)

    def _poll_plan_job(self, job_id, context, poll_generation):
        identity = self._plan_job_identity(context)
        while self._planning_poll_is_current(
            context, poll_generation, job_id
        ):
            try:
                snapshot = self._client.get_plan_job(job_id, identity)
            except AgentConnectionError:
                self._dispatch(
                    lambda session=context, generation=poll_generation, current_job=job_id: self._planning_poll_connection_failed(
                        session, generation, current_job
                    )
                )
                if not self._planning_poll_is_current(
                    context, poll_generation, job_id
                ):
                    return
                threading.Event().wait(1.0)
                continue
            if not self._planning_poll_is_current(
                context, poll_generation, job_id
            ):
                return
            self._dispatch(
                lambda payload=snapshot, session=context, generation=poll_generation: self._apply_plan_job(
                    payload, session, generation
                )
            )
            if snapshot["state"] not in ("queued", "running"):
                return
            threading.Event().wait(1.0)

    def _apply_plan_job(self, snapshot, context, poll_generation):
        job_id = snapshot["job_id"]
        if not self._planning_poll_is_current(
            context, poll_generation, job_id
        ):
            return
        if snapshot["state"] in ("queued", "running"):
            self._set_status("分析中", self._planning_stage_text(snapshot["stage"]))
            return
        if snapshot["state"] == "completed":
            self._planning_terminal_retry_available = False
            self._plan_completed(snapshot["result"], context)
            self._clear_planning_job(context, poll_generation, job_id)
            return
        terminal_messages = {
            "failed": (
                "规划失败",
                (snapshot.get("error") or {}).get(
                    "message", "Agent 未能完成本次规划。"
                ),
            ),
            "cancelled": ("规划已取消", "本次规划任务已取消。"),
            "interrupted": (
                "规划已中断",
                (snapshot.get("error") or {}).get(
                    "message", "Agent 重启或会话中断了本次规划。"
                ),
            ),
        }
        terminal = terminal_messages.get(snapshot["state"])
        if terminal is None:
            return
        title, detail = terminal
        self.Transcript.AppendText("\n[{}] {}\n\n".format(title, detail))
        self._set_busy(False)
        self._set_status(title, detail)
        self.RetryButton.IsEnabled = True
        self._planning_terminal_retry_available = True
        self._clear_planning_job(context, poll_generation, job_id)

    def _planning_poll_connection_failed(
        self, context, poll_generation, job_id
    ):
        if not self._planning_poll_is_current(
            context, poll_generation, job_id
        ):
            return
        self._set_status(
            "仍在等待",
            "仍在等待，本次状态查询失败，正在重新连接…",
        )

    def _planning_submit_connection_failed(self, context, poll_generation):
        if not self._planning_poll_is_current(context, poll_generation):
            return
        self._set_status(
            "仍在等待",
            "规划提交结果未确认，正在使用同一请求重新连接…",
        )

    def _planning_submit_failed(self, message, context, poll_generation):
        if not self._planning_poll_is_current(context, poll_generation):
            return
        self._planning_job_id = None
        self._planning_job_context = None
        self._planning_terminal_retry_available = False
        self._reply_failed(message, True, context)

    def _planning_poll_is_current(
        self, context, poll_generation, job_id=None
    ):
        if (
            not self._session_is_current(context)
            or poll_generation != getattr(self, "_planning_poll_generation", 0)
            or context != getattr(self, "_planning_job_context", None)
        ):
            return False
        return job_id is None or job_id == getattr(self, "_planning_job_id", None)

    def _clear_planning_job(self, context, poll_generation, job_id):
        if not self._planning_poll_is_current(
            context, poll_generation, job_id
        ):
            return
        self._planning_job_id = None
        self._planning_job_context = None

    def _plan_job_identity(self, context):
        return {
            "project_directory": context[1],
            "document_fingerprint": context[2],
            "context_id": context[3],
            "panel_instance_id": self._panel_instance_id,
            "generation": context[0],
            "session_id": context[4],
        }

    @staticmethod
    def _planning_stage_text(stage):
        return {
            "accepted": "规划任务已接收，正在排队…",
            "validating_context": "正在验证当前文档与会话…",
            "reading_model": "正在只读读取 Revit 模型…",
            "capturing_evidence": "正在采集模型证据…",
            "requesting_model": "正在请求 AI 模型分析…",
            "validating_result": "正在校验规划结果…",
            "persisting_result": "正在保存规划结果…",
            "finished": "规划任务已结束。",
        }.get(stage, "规划任务正在进行…")

    def _plan_completed(self, result, context):
        if not self._session_is_current(context):
            return
        self._planning_options = result["options"]
        self.StructuredSummary.Text = result["summary"]
        self.StructuredQuestion.Text = result["question"]
        buttons = self._option_buttons()
        for index, button in enumerate(buttons):
            if index < len(self._planning_options):
                option = self._planning_options[index]
                prefix = "★ " if option["recommended"] else ""
                button.Content = "{}{}\n依据：{}\n影响：{}".format(
                    prefix, option["label"], option["rationale"], option["impact"]
                )
                button.IsEnabled = True
            else:
                button.Content = ""
                button.IsEnabled = False
        self.Transcript.AppendText(
            "AI：{}\n{}\n\n".format(result["summary"], result["question"])
        )
        for option in self._planning_options:
            self.Transcript.AppendText(
                "{}{} — 依据：{}；影响：{}\n".format(
                    "[推荐] " if option["recommended"] else "",
                    option["label"],
                    option["rationale"],
                    option["impact"],
                )
            )
        self.Transcript.AppendText("\n")
        self._set_busy(False)
        self._set_status("等待选择", "可点击方案，或在输入框自由说明项目意图")

    def _select_plan_option(self, index):
        if index >= len(self._planning_options):
            return
        option = self._planning_options[index]
        self._request_plan("选择方案：{}（{}）".format(option["label"], option["id"]))

    def _option_buttons(self):
        return (
            self.Option1Button,
            self.Option2Button,
            self.Option3Button,
            self.Option4Button,
        )

    def _set_plan_options_enabled(self, enabled):
        if not hasattr(self, "Option1Button"):
            return
        for index, button in enumerate(self._option_buttons()):
            button.IsEnabled = bool(enabled and index < len(self._planning_options))

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
        if hasattr(self, "AnalyzeButton"):
            self.AnalyzeButton.IsEnabled = not busy and session_ready
        if hasattr(self, "AnalyzeSelectionButton"):
            self.AnalyzeSelectionButton.IsEnabled = (
                not busy
                and session_ready
                and bool(getattr(self, "_selected_elements", []))
            )
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
        self.NewSessionButton.IsEnabled = False
        self._set_session_status(self._session_detail(detail))
        self._set_busy(False)
        self._set_status("已连接", "当前文档会话已就绪")

    def _pause_session_for_document_change(self, after_revoke=None):
        next_version = self._session_request_version + 1
        context_id = self._session_context_id
        planning_job_id = getattr(self, "_planning_job_id", None)
        planning_job_context = getattr(self, "_planning_job_context", None)
        self._session_request_version = next_version
        self._session_id = None
        self._session_context_id = None
        self._pending_session_id = None
        self._session_project_directory = None
        self._session_document_fingerprint = None
        self._data_root = None
        self._planning_active = False
        self._planning_poll_generation = (
            getattr(self, "_planning_poll_generation", 0) + 1
        )
        self._planning_job_id = None
        self._planning_job_context = None
        self._planning_options = []
        self._planning_terminal_retry_available = False
        if hasattr(self, "StructuredSummary"):
            self.StructuredSummary.Text = "文档已切换；旧方案已清除。"
            self.StructuredQuestion.Text = "尚无待确认方案"
            self._set_plan_options_enabled(False)
        self.SendButton.IsEnabled = False
        if hasattr(self, "AnalyzeButton"):
            self.AnalyzeButton.IsEnabled = False
        self.ContinueSessionButton.IsEnabled = False
        self.NewSessionButton.IsEnabled = False
        self._set_session_status("文档已切换；旧会话已暂停，正在读取新文档")
        if context_id is not None or (
            planning_job_id is not None and planning_job_context is not None
        ):
            if after_revoke is not None and context_id is not None:
                self._pending_session_revoke = (
                    next_version,
                    context_id,
                    after_revoke,
                )
            self._run_background(
                lambda: self._cancel_plan_then_revoke(
                    planning_job_id,
                    planning_job_context,
                    next_version,
                    context_id,
                    after_revoke,
                )
            )
        elif after_revoke is not None:
            after_revoke()

    def _cancel_plan_then_revoke(
        self,
        job_id,
        job_context,
        generation,
        context_id,
        callback,
    ):
        if job_id is not None and job_context is not None:
            self._cancel_plan_job(job_id, job_context)
        if context_id is None:
            if callback is not None:
                self._dispatch(
                    lambda: self._continue_after_revoke(generation, callback)
                )
            return
        if callback is None:
            self._revoke_session(generation, context_id)
            return
        self._revoke_then_continue(generation, context_id, callback)

    def _cancel_plan_job(self, job_id, context):
        try:
            self._client.cancel_plan_job(
                job_id, self._plan_job_identity(context)
            )
        except AgentConnectionError:
            # Session revocation remains the authoritative safety fence when
            # this short best-effort cancellation request cannot connect.
            pass

    def _revoke_then_continue(self, generation, context_id, callback):
        if not self._revoke_session(generation, context_id):
            self._dispatch(
                lambda: self._session_revoke_failed(generation)
            )
            return
        self._dispatch(
            lambda: self._continue_after_revoke(generation, callback)
        )

    def _continue_after_revoke(self, generation, callback):
        if generation == self._session_request_version:
            self._pending_session_revoke = None
            callback()

    def _session_revoke_failed(self, generation):
        if generation != self._session_request_version:
            return
        self.SendButton.IsEnabled = False
        self.ContinueSessionButton.IsEnabled = False
        self.NewSessionButton.IsEnabled = False
        self.RefreshDocumentButton.IsEnabled = True
        self._set_session_status(
            "旧会话撤销失败；保持暂停。请点击“验证文档”重试"
        )
        self._set_status(
            "切换暂停",
            "未确认旧会话已撤销，不会验证或打开新文档会话。",
        )

    def _revoke_session(self, generation, context_id):
        try:
            self._client.revoke_session(
                self._panel_instance_id, generation, context_id
            )
            return True
        except AgentConnectionError:
            return False

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

    def _background_failed(self, message):
        self._reply_failed("后台任务失败：{}".format(message), True)

    def _run_background(self, callback):
        def run_safely():
            try:
                callback()
            except Exception as error:
                try:
                    message = "{}".format(error).strip() or error.__class__.__name__
                    self._dispatch(
                        lambda text=message: self._background_failed(text)
                    )
                except Exception:
                    # Do not let error reporting escape an IronPython worker.
                    # pyRevit may otherwise open ScriptOutput from this non-STA
                    # thread and terminate Revit.
                    pass

        worker = threading.Thread(target=run_safely)
        worker.daemon = True
        worker.start()
