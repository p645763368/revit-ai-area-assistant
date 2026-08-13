"""One-shot Agent runtime joining pyRevit identity with live rvt-mcp evidence."""

from typing import Any, Optional

from .binding_state_store import BindingStateStore
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
    previous_pause_reason: Optional[str],
    authorized_document_path: str,
    client: Any,
    binding_store: Optional[BindingStateStore] = None,
) -> dict:
    session = DocumentBindingSession(authorized_document_path)
    current_snapshot = document_snapshot_from_payload(current_payload)
    stored = binding_store.load(current_snapshot.instance_id) if binding_store else None
    restored_payload = stored.get("bound_document") if stored else previous_payload
    restored_pause_reason = stored.get("pause_reason") if stored else previous_pause_reason
    if stored is not None and restored_payload is None:
        raise ValueError("Agent binding state is unreadable; restart Revit to begin a new task")
    if restored_payload is not None:
        session.restore(
            document_snapshot_from_payload(restored_payload),
            pause_reason=restored_pause_reason,
        )
    evidence = read_current_revit_evidence(client)
    response = document_status_response(
        request_id=request_id,
        session=session,
        document_snapshot=current_snapshot,
        rvt_mcp_snapshot=evidence,
    )
    if binding_store is not None:
        binding = response["payload"]
        bound_document = restored_payload or current_payload
        binding_store.save(
            current_snapshot.instance_id,
            bound_document=bound_document,
            pause_reason=binding["pause_reason"],
        )
    return response
