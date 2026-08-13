import types
import unittest

from area_assistant_pyrevit.document_status import collect_document_status


class PyRevitDocumentStatusTests(unittest.TestCase):
    def test_collects_visible_document_evidence_without_modifying_document(self):
        document = types.SimpleNamespace(
            ActiveView=types.SimpleNamespace(
                Id=types.SimpleNamespace(__str__=lambda self: "42"),
                Name="Level 1",
            ),
            ProjectInformation=types.SimpleNamespace(UniqueId="project-id"),
            PathName=r"D:\test\development-copy.rvt",
            Title="Development Copy",
            IsModified=False,
        )

        snapshot = collect_document_status(
            document,
            r"d:\TEST\development-copy.rvt",
            process_id=19880,
        )

        self.assertEqual(snapshot["revit_instance_id"], "revit-19880")
        self.assertEqual(snapshot["active_view"]["name"], "Level 1")
        self.assertTrue(snapshot["authorized_path_match"])
        self.assertFalse(snapshot["is_modified"])
        self.assertTrue(snapshot["document_fingerprint"].startswith("sha256:"))
