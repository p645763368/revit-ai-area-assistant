import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_LIB = ROOT / "pyrevit" / "AI Area Assistant.extension" / "lib"
sys.path.insert(0, str(EXTENSION_LIB))

from area_assistant_revit.agent_bridge import AgentBridge


class PyRevitAgentBridgeTests(unittest.TestCase):
    def test_bridge_sends_document_snapshot_and_returns_agent_binding(self):
        captured = {}

        def runner(command, input_text, cwd):
            captured["command"] = command
            captured["request"] = json.loads(input_text)
            captured["cwd"] = cwd
            return (
                0,
                json.dumps(
                    {
                        "contract_version": "1.0",
                        "message_type": "response",
                        "request_id": "req-test",
                        "status": "completed",
                        "payload": {
                            "binding_status": "bound",
                            "write_allowed": True,
                            "rvt_mcp_status": "verified",
                            "pause_reason": None,
                        },
                    }
                ),
                "",
            )

        bridge = AgentBridge(
            python_executable=r"C:\Python\python.exe",
            repository_root=r"D:\area-assistant",
            runner=runner,
        )
        current = {
            "revit_instance_id": "revit-4312",
            "document_path": r"D:\RevitTests\development-copy.rvt",
        }

        response = bridge.query(current)

        self.assertEqual(response["payload"]["binding_status"], "bound")
        self.assertEqual(captured["command"][-3:], ["-m", "area_assistant_agent", "--document-status"])
        self.assertEqual(captured["request"]["current_document"], current)
        self.assertIsNone(captured["request"]["previous_document"])

    def test_bridge_remembers_last_bound_document_for_switch_detection(self):
        requests = []

        def runner(command, input_text, cwd):
            request = json.loads(input_text)
            requests.append(request)
            return (
                0,
                json.dumps(
                    {
                        "contract_version": "1.0",
                        "message_type": "response",
                        "request_id": request["request_id"],
                        "status": "completed",
                        "payload": {"binding_status": "bound", "write_allowed": True},
                    }
                ),
                "",
            )

        bridge = AgentBridge("python", str(ROOT), runner=runner)
        first = {"document_fingerprint": "sha256:first"}
        bridge.query(first)
        bridge.query({"document_fingerprint": "sha256:second"})

        self.assertEqual(requests[1]["previous_document"], first)


if __name__ == "__main__":
    unittest.main()
