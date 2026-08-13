"""Public Agent response for the Revit document status action."""

from . import CONTRACT_VERSION
from .document_binding import DocumentBindingSession, DocumentSnapshot, RvtMcpSnapshot


def document_status_response(
    request_id: str,
    session: DocumentBindingSession,
    document_snapshot: DocumentSnapshot,
    rvt_mcp_snapshot: RvtMcpSnapshot,
) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "message_type": "response",
        "request_id": request_id,
        "status": "completed",
        "payload": session.update(document_snapshot, rvt_mcp_snapshot),
    }
