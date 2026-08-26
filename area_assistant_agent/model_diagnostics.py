"""Persist value-free model protocol diagnostics for a local session."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict


def persist_model_diagnostic(
    session_directory: Path,
    job_id: str,
    diagnostic_id: str,
    shape: Dict[str, Any],
) -> Path:
    """Append one structural diagnostic record below the canonical session."""
    diagnostics_directory = Path(session_directory).resolve() / "model_diagnostics"
    diagnostics_directory.mkdir(parents=True, exist_ok=True)
    path = diagnostics_directory / "protocol.jsonl"
    record = {
        "job_id": job_id,
        "diagnostic_id": diagnostic_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "shape": shape,
    }
    _append_jsonl_locked(path, record)
    return path


def _append_jsonl_locked(path: Path, value: Dict[str, Any]) -> None:
    """Serialize complete JSONL records under a local advisory file lock."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_stream:
        if lock_path.stat().st_size == 0:
            lock_stream.write("0")
            lock_stream.flush()
        lock_stream.seek(0)
        _lock(lock_stream)
        try:
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            _unlock(lock_stream)


def _lock(stream: Any) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock(stream: Any) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
