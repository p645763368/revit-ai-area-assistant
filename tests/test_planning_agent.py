import json
import copy
from pathlib import Path
import tempfile
import unittest

from area_assistant_agent.planning import (
    KnowledgeCatalog,
    PlanningAgent,
    PlanningResult,
    READ_ONLY_QUERIES,
    ReadOnlyRevitTools,
)
from area_assistant_agent.document_binding import document_fingerprint
from area_assistant_agent.rvt_mcp_gateway import DOCUMENT_EVIDENCE_CODE


ROOT = Path(__file__).resolve().parents[1]


class _ScriptedModel:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def planning_turn(self, messages, tools):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        return self.turns.pop(0)


class _McpClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "revit_list_available_targets":
            return {"count": 1, "targets": [{"year": 2026, "pid": 1234}]}
        if name == "revit_switch_target":
            return {"ok": True, "verified": True}
        if name == "revit_send_code_to_revit":
            if arguments["code"] == DOCUMENT_EVIDENCE_CODE:
                return {"executed": True, "result": {
                    "documentTitle": "Development Copy",
                    "documentPath": "C:\\Models\\Development Copy.rvt",
                    "projectInformationId": "project-a",
                    "isModified": False,
                    "activeViewId": "42",
                    "activeViewName": "Level 1",
                }}
            return {"executed": True, "result": {"levels": [{"id": "12", "name": "一层"}]}}
        if name == "capture_view_image":
            Path(arguments["output_path"]).write_bytes(b"png")
            return {"view_id": 42, "saved_path": arguments["output_path"]}
        raise AssertionError(name)

    def list_tools(self):
        return {
            "revit_list_available_targets",
            "revit_switch_target",
            "revit_send_code_to_revit",
            "capture_view_image",
        }


class PlanningAgentTests(unittest.TestCase):
    def test_versioned_rules_and_anonymized_cases_are_loaded(self):
        catalog = KnowledgeCatalog(ROOT / "knowledge")

        snapshot = catalog.load()

        self.assertEqual(snapshot["rules"][0]["version"], "1.0.0")
        self.assertEqual(snapshot["cases"][0]["version"], "1.0.0")
        self.assertIn("applicability", snapshot["rules"][0])
        self.assertNotIn("element_id", json.dumps(snapshot, ensure_ascii=False).lower())

    def test_agent_can_inspect_and_capture_before_returning_structured_options(self):
        model = _ScriptedModel(
            [
                {"content": None, "tool_calls": [{"id": "call-1", "name": "inspect_revit_model", "arguments": {"query": "levels"}}]},
                {"content": None, "tool_calls": [{"id": "call-2", "name": "capture_revit_view", "arguments": {"view_id": 42}}]},
                {
                    "content": json.dumps(
                        {
                            "summary": "一层存在两个候选边界来源。",
                            "question": "本轮采用哪个来源继续比较？",
                            "options": [
                                {"id": "floor", "label": "采用楼板", "recommended": True, "rationale": "轮廓完整", "impact": "按楼板外轮廓继续"},
                                {"id": "walls", "label": "采用外墙", "recommended": False, "rationale": "可交叉核对", "impact": "需要检查墙连接"},
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    "tool_calls": [],
                },
            ]
        )
        mcp = _McpClient()
        events = []
        with tempfile.TemporaryDirectory() as directory:
            agent = PlanningAgent(
                model,
                KnowledgeCatalog(ROOT / "knowledge"),
                lambda: mcp,
            )

            result = agent.plan(
                [{"role": "user", "content": "扫描一层"}],
                Path(directory),
                lambda name, inputs, output, error: events.append((name, inputs, output, error)),
            )

        self.assertIsInstance(result, PlanningResult)
        self.assertEqual(len(result.options), 2)
        self.assertTrue(result.options[0]["recommended"])
        self.assertEqual([event[0] for event in events], ["inspect_revit_model", "capture_revit_view"])
        model_reads = [
            call for call in mcp.calls
            if call[0] == "revit_send_code_to_revit"
            and call[1].get("code") != DOCUMENT_EVIDENCE_CODE
        ]
        self.assertEqual(len(model_reads), 1)
        self.assertEqual([call[0] for call in mcp.calls].count("capture_view_image"), 1)
        capture_call = next(call for call in mcp.calls if call[0] == "capture_view_image")
        self.assertTrue(Path(capture_call[1]["output_path"]).is_relative_to(Path(tempfile.gettempdir())))
        self.assertTrue(any(isinstance(message.get("content"), list) for message in model.requests[-1][0]))
        assistant_tool_message = next(
            message for message in model.requests[1][0]
            if message.get("role") == "assistant" and message.get("tool_calls")
        )
        self.assertEqual(
            assistant_tool_message["tool_calls"][0]["function"]["name"],
            "inspect_revit_model",
        )

    def test_read_only_boundary_rejects_unknown_or_write_shaped_tools(self):
        tools = ReadOnlyRevitTools(_McpClient(), Path(tempfile.gettempdir()))

        with self.assertRaisesRegex(ValueError, "read-only"):
            tools.execute("revit_send_code_to_revit", {"code": "delete everything"})
        with self.assertRaisesRegex(ValueError, "read-only"):
            tools.execute("inspect_revit_model", {"query": "delete"})

    def test_boundary_candidates_expose_exact_curve_topology_not_bounding_boxes(self):
        code = READ_ONLY_QUERIES["boundary_candidates"]

        self.assertIn("GetEdgesAsCurveLoops", code)
        self.assertIn("LocationCurve", code)
        self.assertIn("OST_AreaSchemeLines", code)
        self.assertNotIn("get_BoundingBox", code)

    def test_boundary_candidates_only_request_top_faces_from_supported_hosts(self):
        code = READ_ONLY_QUERIES["boundary_candidates"]

        self.assertIn("host is Floor || host is RoofBase", code)
        self.assertIn("HostObjectUtils.GetTopFaces(host)", code)

    def test_parallel_tool_results_precede_visual_follow_up_message(self):
        final = json.dumps(
            {
                "summary": "证据已读取。",
                "question": "选择哪个来源？",
                "options": [
                    {"id": "a", "label": "A", "recommended": True, "rationale": "r", "impact": "i"},
                    {"id": "b", "label": "B", "recommended": False, "rationale": "r", "impact": "i"},
                ],
            }
        )
        model = _ScriptedModel(
            [
                {
                    "content": None,
                    "tool_calls": [
                        {"id": "image", "name": "capture_revit_view", "arguments": {}},
                        {"id": "read", "name": "inspect_revit_model", "arguments": {"query": "overview"}},
                    ],
                },
                {"content": final, "tool_calls": []},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            PlanningAgent(model, KnowledgeCatalog(ROOT / "knowledge"), _McpClient).plan(
                [{"role": "user", "content": "scan"}], Path(directory),
                lambda *args: None,
            )

        roles = [message["role"] for message in model.requests[1][0][-4:]]
        self.assertEqual(roles, ["assistant", "tool", "tool", "user"])

    def test_structured_result_requires_two_to_four_options_and_one_recommendation(self):
        invalid = {
            "summary": "invalid",
            "question": "pick",
            "options": [{"id": "one", "label": "One", "recommended": False, "rationale": "x", "impact": "y"}],
        }

        with self.assertRaises(ValueError):
            PlanningResult.from_dict(invalid)

    def test_screenshot_staging_file_is_removed_when_copy_cannot_complete(self):
        class BrokenCaptureClient(_McpClient):
            def call_tool(self, name, arguments):
                result = super().call_tool(name, arguments)
                if name == "capture_view_image":
                    result["saved_path"] = arguments["output_path"] + ".missing"
                return result

        client = BrokenCaptureClient()
        with tempfile.TemporaryDirectory() as directory:
            tools = ReadOnlyRevitTools(client, Path(directory))
            with self.assertRaisesRegex(RuntimeError, "escaped"):
                tools.execute("capture_revit_view", {})

        capture = next(call for call in client.calls if call[0] == "capture_view_image")
        self.assertFalse(Path(capture[1]["output_path"]).exists())

    def test_screenshot_rejects_external_return_path_without_deleting_it(self):
        class RedirectedCaptureClient(_McpClient):
            def __init__(self, external_path):
                super().__init__()
                self.external_path = external_path

            def call_tool(self, name, arguments):
                result = super().call_tool(name, arguments)
                if name == "capture_view_image":
                    result["saved_path"] = str(self.external_path)
                return result

        with tempfile.TemporaryDirectory() as directory:
            external_path = Path(directory) / "must-survive.txt"
            external_path.write_text("private", encoding="utf-8")
            tools = ReadOnlyRevitTools(
                RedirectedCaptureClient(external_path), Path(directory) / "captures"
            )

            with self.assertRaisesRegex(RuntimeError, "escaped"):
                tools.execute("capture_revit_view", {})

            self.assertEqual(external_path.read_text(encoding="utf-8"), "private")

    def test_screenshot_timeout_degrades_to_structured_plan_without_retrying_mcp(self):
        class TimeoutCaptureClient(_McpClient):
            def call_tool(self, name, arguments):
                if name == "capture_view_image":
                    self.calls.append((name, arguments))
                    raise TimeoutError("rvt-mcp response timed out")
                return super().call_tool(name, arguments)

        final = json.dumps(
            {
                "summary": "结构化几何已读取；视觉证据暂不可用。",
                "question": "在缺少截图的情况下采用哪个来源继续核对？",
                "options": [
                    {
                        "id": "floor",
                        "label": "采用楼板",
                        "recommended": True,
                        "rationale": "精确轮廓可用",
                        "impact": "继续结构化核对",
                    },
                    {
                        "id": "ask",
                        "label": "等待视觉证据",
                        "recommended": False,
                        "rationale": "截图当前不可用",
                        "impact": "修复环境后重试",
                    },
                ],
            },
            ensure_ascii=False,
        )
        model = _ScriptedModel(
            [
                {
                    "content": None,
                    "tool_calls": [{
                        "id": "geometry",
                        "name": "inspect_revit_model",
                        "arguments": {"query": "boundary_candidates"},
                    }],
                },
                {
                    "content": None,
                    "tool_calls": [
                        {"id": "image-1", "name": "capture_revit_view", "arguments": {}}
                    ],
                },
                {
                    "content": None,
                    "tool_calls": [
                        {"id": "image-2", "name": "capture_revit_view", "arguments": {}}
                    ],
                },
                {"content": final, "tool_calls": []},
            ]
        )
        mcp = TimeoutCaptureClient()
        events = []

        with tempfile.TemporaryDirectory() as directory:
            result = PlanningAgent(
                model, KnowledgeCatalog(ROOT / "knowledge"), lambda: mcp
            ).plan(
                [{"role": "user", "content": "scan"}],
                Path(directory),
                lambda name, inputs, output, error: events.append(
                    (name, inputs, output, error)
                ),
            )

        self.assertEqual(len(result.options), 2)
        self.assertIn("视觉证据暂不可用", result.summary)
        self.assertEqual(
            [name for name, _ in mcp.calls].count("capture_view_image"), 1
        )
        self.assertEqual([event[0] for event in events], [
            "inspect_revit_model", "capture_revit_view", "capture_revit_view"
        ])
        tool_results = [
            message
            for request, _ in model.requests[1:]
            for message in request
            if message.get("role") == "tool"
        ]
        self.assertTrue(any("Do not retry capture" in item["content"] for item in tool_results))

    def test_missing_capture_capability_uses_geometry_without_calling_screenshot(self):
        class NoCaptureClient(_McpClient):
            def list_tools(self):
                return {"revit_send_code_to_revit"}

        final = json.dumps({
            "summary": "已取得结构化边界；视觉证据不可用。",
            "question": "采用哪个来源继续？",
            "options": [
                {"id": "a", "label": "楼板", "recommended": True, "rationale": "边界曲线可用", "impact": "继续核对"},
                {"id": "b", "label": "等待截图", "recommended": False, "rationale": "视觉证据缺失", "impact": "稍后复核"},
            ],
        }, ensure_ascii=False)
        model = _ScriptedModel([
            {"content": None, "tool_calls": [{"id": "read", "name": "inspect_revit_model", "arguments": {"query": "boundary_candidates"}}]},
            {"content": None, "tool_calls": [{"id": "image", "name": "capture_revit_view", "arguments": {}}]},
            {"content": final, "tool_calls": []},
        ])
        mcp = NoCaptureClient()

        with tempfile.TemporaryDirectory() as directory:
            result = PlanningAgent(model, KnowledgeCatalog(ROOT / "knowledge"), lambda: mcp).plan(
                [{"role": "user", "content": "scan"}], Path(directory), lambda *_: None
            )

        self.assertEqual(len(result.options), 2)
        self.assertFalse(any(name == "capture_view_image" for name, _ in mcp.calls))
        self.assertEqual([item["function"]["name"] for item in model.requests[0][1]], ["inspect_revit_model"])

    def test_missing_capture_without_boundary_geometry_rejects_recommendations(self):
        class NoCaptureClient(_McpClient):
            def list_tools(self):
                return {"revit_send_code_to_revit"}

        final = json.dumps({
            "summary": "猜测方案",
            "question": "采用哪个？",
            "options": [
                {"id": "a", "label": "A", "recommended": True, "rationale": "猜测", "impact": "未知"},
                {"id": "b", "label": "B", "recommended": False, "rationale": "猜测", "impact": "未知"},
            ],
        })

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "no structured boundary geometry"):
                PlanningAgent(
                    _ScriptedModel([{"content": final, "tool_calls": []}]),
                    KnowledgeCatalog(ROOT / "knowledge"),
                    NoCaptureClient,
                ).plan([{"role": "user", "content": "scan"}], Path(directory), lambda *_: None)

    def test_screenshot_session_guard_failure_remains_fail_closed(self):
        model = _ScriptedModel(
            [{
                "content": None,
                "tool_calls": [
                    {"id": "image", "name": "capture_revit_view", "arguments": {}}
                ],
            }]
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "stale session"):
                PlanningAgent(
                    model, KnowledgeCatalog(ROOT / "knowledge"), lambda: _McpClient()
                ).plan(
                    [{"role": "user", "content": "scan"}],
                    Path(directory),
                    lambda *_: None,
                    session_guard=lambda: (_ for _ in ()).throw(
                        RuntimeError("stale session")
                    ),
                )

    def test_stale_session_guard_blocks_tool_before_any_model_read(self):
        model = _ScriptedModel([{
            "content": None,
            "tool_calls": [{"id": "late", "name": "inspect_revit_model", "arguments": {"query": "overview"}}],
        }])
        mcp = _McpClient()

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "stale"):
                PlanningAgent(model, KnowledgeCatalog(ROOT / "knowledge"), lambda: mcp).plan(
                    [{"role": "user", "content": "scan"}],
                    Path(directory),
                    lambda *args: None,
                    session_guard=lambda: (_ for _ in ()).throw(RuntimeError("stale session")),
                )

        self.assertFalse(any(name == "revit_send_code_to_revit" for name, _ in mcp.calls))

    def test_document_change_during_query_discards_result_before_model_reuse(self):
        class SwitchingDocumentClient(_McpClient):
            def __init__(self):
                super().__init__()
                self.evidence_count = 0

            def call_tool(self, name, arguments):
                if name == "revit_send_code_to_revit" and arguments["code"] == DOCUMENT_EVIDENCE_CODE:
                    self.calls.append((name, arguments))
                    self.evidence_count += 1
                    project = "project-a" if self.evidence_count == 1 else "project-b"
                    return {"executed": True, "result": {
                        "documentTitle": "Development Copy",
                        "documentPath": "C:\\Models\\Development Copy.rvt",
                        "projectInformationId": project,
                        "isModified": False,
                        "activeViewId": "42",
                        "activeViewName": "Level 1",
                    }}
                return super().call_tool(name, arguments)

        model = _ScriptedModel([{
            "content": None,
            "tool_calls": [{"id": "read", "name": "inspect_revit_model", "arguments": {"query": "overview"}}],
        }])
        mcp = SwitchingDocumentClient()
        expected = document_fingerprint(
            "C:\\Models\\Development Copy.rvt", "Development Copy", "project-a"
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "document changed"):
                PlanningAgent(model, KnowledgeCatalog(ROOT / "knowledge"), lambda: mcp).plan(
                    [{"role": "user", "content": "scan"}],
                    Path(directory),
                    lambda *args: None,
                    document_fingerprint=expected,
                )

        self.assertEqual(len(model.requests), 1)


if __name__ == "__main__":
    unittest.main()
