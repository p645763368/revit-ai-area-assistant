"""Collect a document snapshot without opening a Revit transaction."""

import hashlib
import ntpath
import os


def _canonical_path(path):
    if not path or not ntpath.isabs(path):
        return ""
    return ntpath.normcase(ntpath.normpath(path))


def _fingerprint(document_path, document_title, project_information_id):
    identity = u"{0}|{1}|{2}".format(
        _canonical_path(document_path),
        document_title or "",
        project_information_id or "",
    )
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def collect_document_status(document, authorized_document_path, process_id=None):
    active_view = document.ActiveView
    project_information = getattr(document, "ProjectInformation", None)
    project_information_id = getattr(project_information, "UniqueId", "")
    document_path = document.PathName or ""
    authorized_path = _canonical_path(authorized_document_path)
    current_path = _canonical_path(document_path)
    instance_id = "revit-{0}".format(process_id if process_id is not None else os.getpid())

    return {
        "revit_instance_id": instance_id,
        "document_title": document.Title,
        "document_path": document_path,
        "document_fingerprint": _fingerprint(
            document_path,
            document.Title,
            project_information_id,
        ),
        "active_view": {
            "id": str(active_view.Id),
            "name": active_view.Name,
        },
        "is_modified": bool(document.IsModified),
        "authorized_path_match": bool(authorized_path) and current_path == authorized_path,
    }
