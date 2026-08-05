import unittest
import time

from area_assistant_agent.rvt_mcp_gateway import McpStdioClient, read_current_revit_evidence


class FakeMcpClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "revit_list_available_targets":
            return {
                "count": 1,
                "targets": [{"year": "2026", "pid": 4312}],
            }
        if name == "revit_switch_target":
            return {"ok": True, "verified": True}
        if name == "revit_send_code_to_revit":
            return {
                "executed": True,
                "result": {
                    "documentTitle": "Development Copy",
                    "documentPath": r"D:\RevitTests\development-copy.rvt",
                    "projectInformationId": "project-id",
                    "activeViewId": "2178223",
                    "activeViewName": "GFA Review",
                    "isModified": False,
                },
            }
        raise AssertionError("unexpected tool: " + name)


class RvtMcpGatewayTests(unittest.TestCase):
    def test_agent_reads_and_verifies_the_single_live_revit_target(self):
        client = FakeMcpClient()

        evidence = read_current_revit_evidence(client)

        self.assertEqual(evidence.instance_pid, 4312)
        self.assertEqual(evidence.document_title, "Development Copy")
        self.assertEqual(evidence.active_view_id, "2178223")
        self.assertEqual(
            [name for name, _ in client.calls],
            [
                "revit_list_available_targets",
                "revit_switch_target",
                "revit_send_code_to_revit",
            ],
        )

    def test_agent_refuses_ambiguous_multiple_revit_targets(self):
        class MultipleTargetsClient(FakeMcpClient):
            def call_tool(self, name, arguments):
                if name == "revit_list_available_targets":
                    return {
                        "count": 2,
                        "targets": [
                            {"year": "2025", "pid": 100},
                            {"year": "2026", "pid": 200},
                        ],
                    }
                return super().call_tool(name, arguments)

        with self.assertRaisesRegex(RuntimeError, "exactly one Revit target"):
            read_current_revit_evidence(MultipleTargetsClient())

    def test_mcp_client_times_out_when_server_stalls(self):
        client = McpStdioClient(["unused"], timeout_seconds=0.01)

        with self.assertRaisesRegex(RuntimeError, "response timed out"):
            client._read()

    def test_mcp_timeout_is_a_single_deadline_for_the_whole_session(self):
        client = McpStdioClient(["unused"], timeout_seconds=0.01)
        client._deadline = time.monotonic() - 1

        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "response timed out"):
            client._read()

        self.assertLess(time.monotonic() - started, 0.1)


if __name__ == "__main__":
    unittest.main()
