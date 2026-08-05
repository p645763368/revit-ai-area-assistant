import unittest

from area_assistant_agent.document_status_runtime import resolve_document_status


class FakeMcpClient:
    def call_tool(self, name, arguments):
        if name == "revit_list_available_targets":
            return {"count": 1, "targets": [{"year": "2026", "pid": 4312}]}
        if name == "revit_switch_target":
            return {"ok": True, "verified": True}
        if name == "revit_get_current_view_info":
            return {"viewId": 12345, "viewName": "GFA Review"}
        raise AssertionError("unexpected tool: " + name)


def payload(path=r"D:\RevitTests\development-copy.rvt", fingerprint="sha256:initial"):
    return {
        "revit_instance_id": "revit-4312",
        "document_title": "Development Copy",
        "document_path": path,
        "document_fingerprint": fingerprint,
        "active_view": {"id": "12345", "name": "GFA Review"},
        "is_modified": False,
    }


class DocumentStatusRuntimeTests(unittest.TestCase):
    def test_one_shot_agent_runtime_returns_verified_binding(self):
        response = resolve_document_status(
            request_id="req-1",
            current_payload=payload(),
            previous_payload=None,
            authorized_document_path=r"D:\RevitTests\development-copy.rvt",
            client=FakeMcpClient(),
        )

        self.assertEqual(response["payload"]["binding_status"], "bound")
        self.assertEqual(response["payload"]["rvt_mcp_status"], "verified")
        self.assertIs(response["payload"]["write_allowed"], True)

    def test_previous_binding_causes_switched_document_to_pause(self):
        response = resolve_document_status(
            request_id="req-2",
            current_payload=payload(
                path=r"D:\RevitTests\another-model.rvt",
                fingerprint="sha256:another",
            ),
            previous_payload=payload(),
            authorized_document_path=r"D:\RevitTests\development-copy.rvt",
            client=FakeMcpClient(),
        )

        self.assertEqual(response["payload"]["binding_status"], "paused")
        self.assertEqual(response["payload"]["pause_reason"], "document_changed")
        self.assertIs(response["payload"]["write_allowed"], False)


if __name__ == "__main__":
    unittest.main()
