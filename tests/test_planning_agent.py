import json
import copy
from pathlib import Path
import tempfile
import unittest

from area_assistant_agent.planning import (
    KnowledgeCatalog,
    PlanningAgent,
    PlanningResult,
    ReadOnlyRevitTools,
)


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
            return {"count": 1, "targets": [{"year": 2026}]}
        if name == "revit_switch_target":
            return {"ok": True, "verified": True}
        if name == "revit_send_code_to_revit":
            return {"executed": True, "result": {"levels": [{"id": "12", "name": "一层"}]}}
        if name == "capture_view_image":
            Path(arguments["output_path"]).write_bytes(b"png")
            return {"view_id": 42, "saved_path": arguments["output_path"]}
        raise AssertionError(name)


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
        self.assertEqual([call[0] for call in mcp.calls].count("revit_send_code_to_revit"), 1)
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

    def test_structured_result_requires_two_to_four_options_and_one_recommendation(self):
        invalid = {
            "summary": "invalid",
            "question": "pick",
            "options": [{"id": "one", "label": "One", "recommended": False, "rationale": "x", "impact": "y"}],
        }

        with self.assertRaises(ValueError):
            PlanningResult.from_dict(invalid)


if __name__ == "__main__":
    unittest.main()
