import json
from pathlib import Path
import tempfile
import threading
from urllib.request import Request, urlopen
import unittest

from area_assistant_agent.config import AgentConfig
from area_assistant_agent.planning import PlanningResult
from area_assistant_agent.server import create_server


class _PlanningAgent:
    def __init__(self):
        self.conversations = []

    def plan(self, conversation, session_directory, audit, **kwargs):
        self.conversations.append(conversation)
        audit("inspect_revit_model", {"query": "overview"}, {"areaCount": 0}, None)
        return PlanningResult.from_dict(
            {
                "summary": "已完成只读扫描。",
                "question": "下一步比较哪类来源？",
                "options": [
                    {"id": "floor", "label": "楼板", "recommended": True, "rationale": "结构化轮廓可用", "impact": "继续比较楼板轮廓"},
                    {"id": "wall", "label": "墙体", "recommended": False, "rationale": "作为交叉证据", "impact": "增加墙连接检查"},
                ],
            }
        )


class AgentPlanningApiTests(unittest.TestCase):
    def setUp(self):
        config = AgentConfig("127.0.0.1", 0, "http://127.0.0.1:1/v1", "unit-test", "test-model", 1)
        self.server = create_server(config)
        self.fake = _PlanningAgent()
        self.server.planning_agent = self.fake
        self.server.current_document_status = {
            "binding_status": "bound",
            "rvt_mcp_status": "verified",
            "document_fingerprint": "document-a",
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_selected_option_continues_with_prior_conversation_in_same_session(self):
        with tempfile.TemporaryDirectory() as project_directory:
            opened = self._post("/v1/sessions/open", "session.open", {
                "document_fingerprint": "document-a", "generation": 1,
                "panel_instance_id": "panel-a", "project_directory": project_directory,
            })
            chosen = self._post("/v1/sessions/choose", "session.choose", {
                "choice": "new", "context_id": opened["context_id"],
                "document_fingerprint": "document-a", "generation": 1,
                "panel_instance_id": "panel-a", "project_directory": project_directory,
                "session_id": None,
            })
            common = {
                "context_id": chosen["context_id"], "document_fingerprint": "document-a",
                "generation": 1, "panel_instance_id": "panel-a",
                "project_directory": project_directory, "session_id": chosen["active_session_id"],
            }

            first = self._post("/v1/plans", "analysis.plan", dict(common, message="扫描当前模型"))
            second = self._post("/v1/plans", "analysis.plan", dict(common, message="选择：楼板"))

            self.assertEqual(len(first["options"]), 2)
            self.assertTrue(first["options"][0]["recommended"])
            second_history = self.fake.conversations[1]
            self.assertEqual([item["role"] for item in second_history], ["user", "assistant", "user"])
            self.assertIn("扫描当前模型", second_history[0]["content"])
            self.assertIn("选择：楼板", second_history[-1]["content"])
            operations = next(Path(project_directory).glob("AI_Area_Assistant_Data/documents/*/sessions/*/operations.jsonl"))
            self.assertEqual(len(operations.read_text(encoding="utf-8").splitlines()), 2)

    def _post(self, path, action, payload):
        request_id = "req-" + action
        request = Request(
            "http://127.0.0.1:{}{}".format(self.server.server_port, path),
            data=json.dumps({
                "contract_version": "1.0", "message_type": "request",
                "request_id": request_id, "action": action, "payload": payload,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=2) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        self.assertEqual(envelope["request_id"], request_id)
        return envelope["payload"]


if __name__ == "__main__":
    unittest.main()
