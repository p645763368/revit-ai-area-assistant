"""Show the current Revit document identity and safe binding status."""

import os
import sys

from pyrevit import forms, revit


extension_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
extension_lib = os.path.join(extension_root, "lib")
if extension_lib not in sys.path:
    sys.path.insert(0, extension_lib)

from area_assistant_revit.document_status import collect_document_status


status = collect_document_status(
    revit.doc,
    os.environ.get("AI_AREA_ASSISTANT_TEST_DOCUMENT", ""),
)
path_match = "yes" if status["authorized_path_match"] else "no"
view = status["active_view"]
message = "\n".join(
    [
        "Revit instance: {0}".format(status["revit_instance_id"]),
        "Document title: {0}".format(status["document_title"]),
        "Document path: {0}".format(status["document_path"] or "<unsaved>"),
        "Active view: {0} ({1})".format(view["name"], view["id"]),
        "IsModified: {0}".format(status["is_modified"]),
        "Authorized path match: {0}".format(path_match),
        "Agent/rvt-mcp binding: pending",
        "Write permission: denied",
    ]
)


forms.alert(
    message,
    title="AI Area Assistant",
    warn_icon=False,
)
