"""One-shot Agent runtime joining pyRevit identity with live rvt-mcp evidence."""

from typing import Any, Optional

from .document_binding import DocumentBindingSession, DocumentSnapshot
from .document_status_action import document_status_response
from .rvt_mcp_gateway import read_current_revit_evidence


def document_snapshot_from_payload(payload: dict) -> DocumentSnapshot:
    try:
        active_view = payload["active_view"]
        return DocumentSnapshot(
            instance_id=str(payload["revit_instance_id"]),
            document_title=str(payload["document_title"]),
            document_path=str(payload["document_path"]),
            document_fingerprint=str(payload["document_fingerprint"]),
            active_view_id=str(active_view["id"]),
            active_view_name=str(active_view["name"]),
            is_modified=bool(payload["is_modified"]),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("pyRevit document snapshot is incomplete") from error


def resolve_document_status(
    request_id: str,
    current_payload: dict,
    previous_payload: Optional[dict],
    authorized_document_path: str,
    client: Any,
) -> dict:
    session = DocumentBindingSession(authorized_document_path)
    if previous_payload is not None:
        session.restore(document_snapshot_from_payload(previous_payload))
    evidence = read_current_revit_evidence(client)
    return document_status_response(
        request_id=request_id,
        session=session,
        document_snapshot=document_snapshot_from_payload(current_payload),
        rvt_mcp_snapshot=evidence,
    )
