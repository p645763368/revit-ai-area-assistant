import unittest
from pathlib import Path
import tempfile

from area_assistant_agent.binding_state_store import BindingStateStore
from area_assistant_agent.document_status_runtime import resolve_document_status
from area_assistant_agent.document_binding import document_fingerprint


class FakeMcpClient:
    def call_tool(self, name, arguments):
        if name == "revit_list_available_targets":
            return {"count": 1, "targets": [{"year": "2026", "pid": 4312}]}
        if name == "revit_switch_target":
            return {"ok": True, "verified": True}
        if name == "revit_send_code_to_revit":
            return {
                "executed": True,
                "result": {
                    "documentTitle": "Development Copy",
                    "documentPath": r"D:\RevitTests\development-copy.rvt",
                    "projectInformationId": "project-id",
                    "activeViewId": "12345",
                    "activeViewName": "GFA Review",
                    "isModified": False,
                },
            }
        raise AssertionError("unexpected tool: " + name)


def payload(path=r"D:\RevitTests\development-copy.rvt", fingerprint=None):
    if fingerprint is None:
        fingerprint = document_fingerprint(path, "Development Copy", "project-id")
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
            previous_pause_reason=None,
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
            previous_pause_reason=None,
            authorized_document_path=r"D:\RevitTests\development-copy.rvt",
            client=FakeMcpClient(),
        )

        self.assertEqual(response["payload"]["binding_status"], "paused")
        self.assertEqual(response["payload"]["pause_reason"], "document_changed")
        self.assertIs(response["payload"]["write_allowed"], False)

    def test_agent_keeps_prior_pause_latched_after_returning_to_bound_document(self):
        response = resolve_document_status(
            request_id="req-3",
            current_payload=payload(),
            previous_payload=payload(),
            previous_pause_reason="document_changed",
            authorized_document_path=r"D:\RevitTests\development-copy.rvt",
            client=FakeMcpClient(),
        )

        self.assertEqual(response["payload"]["binding_status"], "paused")
        self.assertEqual(response["payload"]["pause_reason"], "document_changed")
        self.assertIs(response["payload"]["write_allowed"], False)

    def test_agent_owned_store_keeps_pause_when_later_caller_omits_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = BindingStateStore(Path(temporary))
            initial = resolve_document_status(
                request_id="req-store-1",
                current_payload=payload(),
                previous_payload=None,
                previous_pause_reason=None,
                authorized_document_path=r"D:\RevitTests\development-copy.rvt",
                client=FakeMcpClient(),
                binding_store=store,
            )
            switched = resolve_document_status(
                request_id="req-store-2",
                current_payload=payload(
                    path=r"D:\RevitTests\another-model.rvt",
                    fingerprint="sha256:another",
                ),
                previous_payload=None,
                previous_pause_reason=None,
                authorized_document_path=r"D:\RevitTests\development-copy.rvt",
                client=FakeMcpClient(),
                binding_store=store,
            )
            returned = resolve_document_status(
                request_id="req-store-3",
                current_payload=payload(),
                previous_payload=None,
                previous_pause_reason=None,
                authorized_document_path=r"D:\RevitTests\development-copy.rvt",
                client=FakeMcpClient(),
                binding_store=store,
            )

        self.assertEqual(initial["payload"]["binding_status"], "bound")
        self.assertEqual(switched["payload"]["binding_status"], "paused")
        self.assertEqual(returned["payload"]["binding_status"], "paused")
        self.assertEqual(returned["payload"]["pause_reason"], "document_changed")
        self.assertIs(returned["payload"]["write_allowed"], False)


if __name__ == "__main__":
    unittest.main()
