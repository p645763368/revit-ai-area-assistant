import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from area_assistant_agent.document_binding import (
    DocumentBindingSession,
    DocumentSnapshot,
    RvtMcpSnapshot,
)
from area_assistant_agent.document_status_action import document_status_response


ROOT = Path(__file__).resolve().parents[1]
ACTION_CONTRACT = ROOT / "contracts" / "v1" / "actions" / "revit-document-status.schema.json"
ACTION_EXAMPLE = ROOT / "contracts" / "v1" / "actions" / "examples" / "revit-document-status.json"


class DocumentStatusContractTests(unittest.TestCase):
    def test_document_status_example_is_valid_and_versioned(self):
        schema = json.loads(ACTION_CONTRACT.read_text(encoding="utf-8"))
        example = json.loads(ACTION_EXAMPLE.read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(example)
        self.assertEqual(example["contract_version"], "1.0")
        self.assertEqual(example["message_type"], "response")
        self.assertEqual(example["payload"]["binding_status"], "bound")

    def test_agent_document_status_response_matches_the_public_contract(self):
        schema = json.loads(ACTION_CONTRACT.read_text(encoding="utf-8"))
        session = DocumentBindingSession(r"D:\RevitTests\development-copy.rvt")
        snapshot = DocumentSnapshot(
            instance_id="revit-4312",
            document_title="Development Copy",
            document_path=r"D:\RevitTests\development-copy.rvt",
            document_fingerprint="sha256:anonymous-document",
            active_view_id="12345",
            active_view_name="GFA Review",
            is_modified=False,
        )

        response = document_status_response(
            request_id="req-42",
            session=session,
            document_snapshot=snapshot,
            rvt_mcp_snapshot=RvtMcpSnapshot(instance_pid=4312),
        )

        Draft202012Validator(schema).validate(response)

    def test_agent_response_pauses_after_document_switch_instead_of_rebinding(self):
        session = DocumentBindingSession(r"D:\RevitTests\development-copy.rvt")
        initial = DocumentSnapshot(
            instance_id="revit-4312",
            document_title="Development Copy",
            document_path=r"D:\RevitTests\development-copy.rvt",
            document_fingerprint="sha256:initial-document",
            active_view_id="12345",
            active_view_name="GFA Review",
            is_modified=False,
        )
        document_status_response(
            "req-1",
            session,
            initial,
            RvtMcpSnapshot(instance_pid=4312),
        )
        switched = DocumentSnapshot(
            instance_id="revit-4312",
            document_title="Another Model",
            document_path=r"D:\RevitTests\another-model.rvt",
            document_fingerprint="sha256:another-document",
            active_view_id="88",
            active_view_name="Floor Plan",
            is_modified=False,
        )

        response = document_status_response(
            "req-2",
            session,
            switched,
            RvtMcpSnapshot(instance_pid=4312),
        )

        self.assertEqual(response["payload"]["binding_status"], "paused")
        self.assertEqual(response["payload"]["pause_reason"], "document_changed")
        self.assertIs(response["payload"]["write_allowed"], False)


if __name__ == "__main__":
    unittest.main()
