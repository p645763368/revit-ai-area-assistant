import unittest

from area_assistant_agent.rvt_mcp_gateway import read_current_revit_evidence


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
        raise AssertionError("unexpected tool: " + name)


class RvtMcpGatewayTests(unittest.TestCase):
    def test_agent_reads_and_verifies_the_single_live_revit_target(self):
        client = FakeMcpClient()

        evidence = read_current_revit_evidence(client)

        self.assertEqual(evidence.instance_pid, 4312)
        self.assertEqual(client.calls, [("revit_list_available_targets", {})])

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


if __name__ == "__main__":
    unittest.main()
