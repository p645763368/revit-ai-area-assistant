import unittest

from area_assistant_agent.document_binding import (
    DocumentBindingSession,
    DocumentSnapshot,
    RvtMcpSnapshot,
)


AUTHORIZED_COPY = r"D:\RevitTests\area-assistant-development-copy.rvt"


def snapshot(
    path=AUTHORIZED_COPY,
    instance_id="revit-4312",
    view_id="2178223",
    modified=False,
    fingerprint="sha256:demo-document",
):
    return DocumentSnapshot(
        instance_id=instance_id,
        document_title="Area Assistant Development Copy",
        document_path=path,
        document_fingerprint=fingerprint,
        active_view_id=view_id,
        active_view_name="GFA Review",
        is_modified=modified,
    )


def rvt_mcp_snapshot(instance_pid=4312, active_view_id="2178223"):
    return RvtMcpSnapshot(instance_pid=instance_pid, active_view_id=active_view_id)


class DocumentBindingSessionTests(unittest.TestCase):
    def test_bound_status_exposes_current_document_and_safe_write_permission(self):
        session = DocumentBindingSession(AUTHORIZED_COPY)

        status = session.bind(snapshot(modified=True), rvt_mcp_snapshot())

        self.assertEqual(status["binding_status"], "bound")
        self.assertEqual(status["revit_instance_id"], "revit-4312")
        self.assertEqual(status["document_path"], AUTHORIZED_COPY)
        self.assertEqual(status["active_view"], {"id": "2178223", "name": "GFA Review"})
        self.assertIs(status["is_modified"], True)
        self.assertIs(status["write_allowed"], True)
        self.assertEqual(status["rvt_mcp_status"], "verified")
        self.assertIsNone(status["pause_reason"])

    def test_rvt_mcp_target_mismatch_pauses_binding_and_denies_writes(self):
        session = DocumentBindingSession(AUTHORIZED_COPY)

        status = session.bind(snapshot(), rvt_mcp_snapshot(instance_pid=9917))

        self.assertEqual(status["binding_status"], "paused")
        self.assertEqual(status["rvt_mcp_status"], "mismatch")
        self.assertEqual(status["pause_reason"], "rvt_mcp_instance_mismatch")
        self.assertIs(status["write_allowed"], False)

    def test_stale_rvt_mcp_view_pauses_binding_and_denies_writes(self):
        session = DocumentBindingSession(AUTHORIZED_COPY)

        status = session.bind(snapshot(), rvt_mcp_snapshot(active_view_id="88"))

        self.assertEqual(status["binding_status"], "paused")
        self.assertEqual(status["rvt_mcp_status"], "mismatch")
        self.assertEqual(status["pause_reason"], "rvt_mcp_view_mismatch")
        self.assertIs(status["write_allowed"], False)

    def test_switching_documents_pauses_the_bound_task_and_revokes_write_permission(self):
        session = DocumentBindingSession(AUTHORIZED_COPY)
        session.bind(snapshot(), rvt_mcp_snapshot())

        status = session.observe(
            snapshot(
                path=r"D:\RevitTests\another-model.rvt",
                view_id="9",
                fingerprint="sha256:another-document",
            )
        )

        self.assertEqual(status["binding_status"], "paused")
        self.assertEqual(status["pause_reason"], "document_changed")
        self.assertIs(status["write_allowed"], False)

    def test_switching_revit_instances_pauses_the_bound_task(self):
        session = DocumentBindingSession(AUTHORIZED_COPY)
        session.bind(snapshot(), rvt_mcp_snapshot())

        status = session.observe(snapshot(instance_id="revit-9917"))

        self.assertEqual(status["binding_status"], "paused")
        self.assertEqual(status["pause_reason"], "revit_instance_changed")
        self.assertIs(status["write_allowed"], False)

    def test_original_model_and_unsaved_document_never_receive_write_permission(self):
        session = DocumentBindingSession(AUTHORIZED_COPY)

        original = session.bind(
            snapshot(path=r"D:\Projects\original-model.rvt"),
            rvt_mcp_snapshot(),
        )
        unsaved = DocumentBindingSession(AUTHORIZED_COPY).bind(
            snapshot(path=""),
            rvt_mcp_snapshot(),
        )

        self.assertIs(original["write_allowed"], False)
        self.assertIs(unsaved["write_allowed"], False)

    def test_relative_authorized_path_is_rejected(self):
        session = DocumentBindingSession("development-copy.rvt")

        status = session.bind(
            snapshot(path="development-copy.rvt"),
            rvt_mcp_snapshot(),
        )

        self.assertIs(status["write_allowed"], False)

    def test_rvt_mcp_snapshot_accepts_discovery_and_current_view_results(self):
        evidence = RvtMcpSnapshot.from_tool_results(
            target={"year": "2026", "pid": 4312},
            current_view={"viewId": 2178223, "viewName": "GFA Review"},
        )

        self.assertEqual(evidence.instance_pid, 4312)
        self.assertEqual(evidence.active_view_id, "2178223")


if __name__ == "__main__":
    unittest.main()
