import json
from pathlib import Path
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_LIB = ROOT / "pyrevit" / "AI Area Assistant.extension" / "lib"
sys.path.insert(0, str(EXTENSION_LIB))

from area_assistant_revit.agent_bridge import AgentBridge, _run_process


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
                        "request_id": captured["request"]["request_id"],
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
            "document_title": "Development Copy",
            "document_path": r"D:\RevitTests\development-copy.rvt",
            "document_fingerprint": "sha256:first",
            "active_view": {"id": "42", "name": "GFA Review"},
            "is_modified": False,
        }

        response = bridge.query(current)

        self.assertEqual(response["payload"]["binding_status"], "bound")
        self.assertEqual(captured["command"][-3:], ["-m", "area_assistant_agent", "--document-status"])
        self.assertEqual(captured["request"]["contract_version"], "1.0")
        self.assertEqual(captured["request"]["message_type"], "request")
        self.assertEqual(captured["request"]["action"], "revit.document_status")
        self.assertEqual(captured["request"]["payload"]["current_document"], current)
        self.assertIsNone(captured["request"]["payload"]["previous_document"])

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
        first = {
            "revit_instance_id": "revit-4312",
            "document_title": "Development Copy",
            "document_path": r"D:\RevitTests\development-copy.rvt",
            "document_fingerprint": "sha256:first",
            "active_view": {"id": "42", "name": "GFA Review"},
            "is_modified": False,
        }
        bridge.query(first)
        second = dict(first)
        second["document_fingerprint"] = "sha256:second"
        bridge.query(second)

        self.assertEqual(requests[1]["payload"]["previous_document"], first)

    def test_bridge_rejects_unsupported_response_contract_version(self):
        def runner(command, input_text, cwd):
            request = json.loads(input_text)
            return (
                0,
                json.dumps(
                    {
                        "contract_version": "2.0",
                        "message_type": "response",
                        "request_id": request["request_id"],
                        "status": "completed",
                        "payload": {},
                    }
                ),
                "",
            )

        bridge = AgentBridge("python", str(ROOT), runner=runner)
        current = {
            "revit_instance_id": "revit-4312",
            "document_title": "Development Copy",
            "document_path": r"D:\RevitTests\development-copy.rvt",
            "document_fingerprint": "sha256:first",
            "active_view": {"id": "42", "name": "GFA Review"},
            "is_modified": False,
        }

        with self.assertRaisesRegex(RuntimeError, "unsupported.*contract version"):
            bridge.query(current)

    def test_background_bridge_returns_pending_without_waiting_for_stalled_runner(self):
        def stalled_runner(command, input_text, cwd):
            time.sleep(0.2)
            return 1, "", ""

        bridge = AgentBridge("python", str(ROOT), runner=stalled_runner, background=True)
        current = {
            "revit_instance_id": "revit-4312",
            "document_title": "Development Copy",
            "document_path": r"D:\RevitTests\development-copy.rvt",
            "document_fingerprint": "sha256:first",
            "active_view": {"id": "42", "name": "GFA Review"},
            "is_modified": False,
        }

        started = time.monotonic()
        response = bridge.query(current)

        self.assertIsNone(response)
        self.assertLess(time.monotonic() - started, 0.1)

    def test_process_runner_times_out_and_terminates_stalled_child(self):
        started = time.monotonic()

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            _run_process(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                "",
                str(ROOT),
                timeout_seconds=0.05,
            )

        self.assertLess(time.monotonic() - started, 1.5)

    def test_bridge_carries_agent_pause_latch_into_later_requests(self):
        requests = []

        def runner(command, input_text, cwd):
            request = json.loads(input_text)
            requests.append(request)
            pause_reason = request["payload"]["previous_pause_reason"]
            current = request["payload"]["current_document"]
            previous = request["payload"]["previous_document"]
            switched = previous is not None and (
                current["document_fingerprint"] != previous["document_fingerprint"]
            )
            pause_reason = pause_reason or ("document_changed" if switched else None)
            return (
                0,
                json.dumps(
                    {
                        "contract_version": "1.0",
                        "message_type": "response",
                        "request_id": request["request_id"],
                        "status": "completed",
                        "payload": {
                            "binding_status": "paused" if pause_reason else "bound",
                            "write_allowed": False if pause_reason else True,
                            "pause_reason": pause_reason,
                        },
                    }
                ),
                "",
            )

        bridge = AgentBridge("python", str(ROOT), runner=runner)
        first = {
            "revit_instance_id": "revit-4312",
            "document_title": "Development Copy",
            "document_path": r"D:\RevitTests\development-copy.rvt",
            "document_fingerprint": "sha256:first",
            "active_view": {"id": "42", "name": "GFA Review"},
            "is_modified": False,
        }
        switched = dict(first)
        switched["document_fingerprint"] = "sha256:second"

        bridge.query(first)
        bridge.query(switched)
        final = bridge.query(first)

        self.assertEqual(final["payload"]["binding_status"], "paused")
        self.assertEqual(
            requests[2]["payload"]["previous_pause_reason"],
            "document_changed",
        )

    def test_new_pyrevit_engine_reads_completed_background_result(self):
        calls = []

        def runner(command, input_text, cwd):
            request = json.loads(input_text)
            calls.append(request)
            return (
                0,
                json.dumps(
                    {
                        "contract_version": "1.0",
                        "message_type": "response",
                        "request_id": request["request_id"],
                        "status": "completed",
                        "payload": {
                            "binding_status": "bound",
                            "write_allowed": True,
                            "pause_reason": None,
                        },
                    }
                ),
                "",
            )

        current = {
            "revit_instance_id": "revit-4312",
            "document_title": "Development Copy",
            "document_path": r"D:\RevitTests\development-copy.rvt",
            "document_fingerprint": "sha256:first",
            "active_view": {"id": "42", "name": "GFA Review"},
            "is_modified": False,
        }
        with tempfile.TemporaryDirectory() as temporary:
            first_engine = AgentBridge(
                "python",
                str(ROOT),
                runner=runner,
                background=True,
                result_root=temporary,
            )
            self.assertIsNone(first_engine.query(current))
            deadline = time.monotonic() + 1
            while first_engine._worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            second_engine = AgentBridge(
                "python",
                str(ROOT),
                runner=lambda *args: self.fail("completed result should be reused"),
                background=True,
                result_root=temporary,
            )

            response = second_engine.query(current)

        self.assertEqual(response["payload"]["binding_status"], "bound")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
