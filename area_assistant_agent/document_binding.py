"""Read-only document identity and single-target binding rules."""

from dataclasses import dataclass
import ntpath
from typing import Optional


def canonical_document_path(path: str) -> str:
    if not path or not ntpath.isabs(path):
        return ""
    return ntpath.normcase(ntpath.normpath(path))


@dataclass(frozen=True)
class DocumentSnapshot:
    instance_id: str
    document_title: str
    document_path: str
    document_fingerprint: str
    active_view_id: str
    active_view_name: str
    is_modified: bool


@dataclass(frozen=True)
class RvtMcpSnapshot:
    instance_pid: int
    active_view_id: str

    @classmethod
    def from_tool_results(cls, target: dict, current_view: dict) -> "RvtMcpSnapshot":
        try:
            instance_pid = int(target["pid"])
            active_view_id = str(current_view["viewId"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("rvt-mcp target and current view results are incomplete") from error
        return cls(instance_pid=instance_pid, active_view_id=active_view_id)


class DocumentBindingSession:
    def __init__(self, authorized_document_path: str):
        self._authorized_path = canonical_document_path(authorized_document_path)
        self._bound_identity: Optional[tuple[str, str, str]] = None
        self._rvt_mcp_status = "unchecked"
        self._paused_reason: Optional[str] = None

    def restore(self, snapshot: DocumentSnapshot) -> None:
        """Restore the last Agent-approved identity for a new one-shot process."""
        self._bound_identity = (
            snapshot.instance_id,
            snapshot.document_fingerprint,
            canonical_document_path(snapshot.document_path),
        )
        self._rvt_mcp_status = "verified"
        self._paused_reason = None

    def bind(self, snapshot: DocumentSnapshot, rvt_mcp_snapshot: RvtMcpSnapshot) -> dict:
        self._paused_reason = None
        self._bound_identity = (
            snapshot.instance_id,
            snapshot.document_fingerprint,
            canonical_document_path(snapshot.document_path),
        )
        self._paused_reason = self._rvt_mcp_pause_reason(snapshot, rvt_mcp_snapshot)
        if self._paused_reason is not None:
            self._rvt_mcp_status = "mismatch"
            return self._status(
                snapshot,
                binding_status="paused",
                pause_reason=self._paused_reason,
            )
        self._rvt_mcp_status = "verified"
        return self._status(snapshot, binding_status="bound", pause_reason=None)

    def update(self, snapshot: DocumentSnapshot, rvt_mcp_snapshot: RvtMcpSnapshot) -> dict:
        if self._bound_identity is None:
            return self.bind(snapshot, rvt_mcp_snapshot)
        return self.observe(snapshot, rvt_mcp_snapshot)

    def observe(
        self,
        snapshot: DocumentSnapshot,
        rvt_mcp_snapshot: Optional[RvtMcpSnapshot] = None,
    ) -> dict:
        if self._bound_identity is None:
            return self._status(snapshot, binding_status="unbound", pause_reason="not_bound")
        if self._paused_reason is not None:
            return self._status(snapshot, binding_status="paused", pause_reason=self._paused_reason)

        bound_instance, bound_fingerprint, bound_path = self._bound_identity
        if snapshot.instance_id != bound_instance:
            self._paused_reason = "revit_instance_changed"
            return self._status(snapshot, binding_status="paused", pause_reason=self._paused_reason)
        if (
            snapshot.document_fingerprint != bound_fingerprint
            or canonical_document_path(snapshot.document_path) != bound_path
        ):
            self._paused_reason = "document_changed"
            return self._status(snapshot, binding_status="paused", pause_reason=self._paused_reason)
        if rvt_mcp_snapshot is not None:
            self._paused_reason = self._rvt_mcp_pause_reason(snapshot, rvt_mcp_snapshot)
            if self._paused_reason is not None:
                self._rvt_mcp_status = "mismatch"
                return self._status(snapshot, binding_status="paused", pause_reason=self._paused_reason)
            self._rvt_mcp_status = "verified"
        return self._status(snapshot, binding_status="bound", pause_reason=None)

    @staticmethod
    def _rvt_mcp_pause_reason(
        snapshot: DocumentSnapshot,
        rvt_mcp_snapshot: RvtMcpSnapshot,
    ) -> Optional[str]:
        if snapshot.instance_id != f"revit-{rvt_mcp_snapshot.instance_pid}":
            return "rvt_mcp_instance_mismatch"
        if snapshot.active_view_id != rvt_mcp_snapshot.active_view_id:
            return "rvt_mcp_view_mismatch"
        return None

    def _status(self, snapshot: DocumentSnapshot, binding_status: str, pause_reason: Optional[str]) -> dict:
        current_path = canonical_document_path(snapshot.document_path)
        return {
            "binding_status": binding_status,
            "revit_instance_id": snapshot.instance_id,
            "document_title": snapshot.document_title,
            "document_path": snapshot.document_path,
            "document_fingerprint": snapshot.document_fingerprint,
            "active_view": {"id": snapshot.active_view_id, "name": snapshot.active_view_name},
            "is_modified": bool(snapshot.is_modified),
            "rvt_mcp_status": self._rvt_mcp_status,
            "write_allowed": (
                binding_status == "bound"
                and self._rvt_mcp_status == "verified"
                and bool(self._authorized_path)
                and current_path == self._authorized_path
            ),
            "pause_reason": pause_reason,
        }
