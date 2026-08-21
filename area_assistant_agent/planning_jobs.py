"""Durable, deduplicated planning-job state for the local Agent."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Optional, Tuple
import uuid

from .persistence import redact_sensitive
from .planning import PlanningResult


IDENTITY_FIELDS = (
    "panel_instance_id",
    "generation",
    "context_id",
    "document_fingerprint",
    "session_id",
)
PUBLIC_SNAPSHOT_FIELDS = (
    "job_id",
    "state",
    "stage",
    "created_at",
    "updated_at",
    "result",
    "error",
)
NON_TERMINAL_STATES = {"queued", "running"}
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
VALID_STATES = NON_TERMINAL_STATES | TERMINAL_STATES
ALLOWED_TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"running", "completed", "failed", "cancelled"},
}
LEGACY_CONFLICT_ERROR_CODE = "legacy_idempotency_conflict"
LEGACY_CONFLICT_ERROR_MESSAGE = (
    "Conflicting legacy planning jobs were interrupted safely."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanningJobRegistry:
    """Own planning snapshots and their atomic, session-local persistence."""

    def __init__(
        self,
        storage_root: Path,
        clock: Optional[Callable[[], Any]] = None,
        id_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.storage_root = Path(storage_root).resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now
        self._id_factory = id_factory or uuid.uuid4
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._idempotency_index: Dict[str, str] = {}
        self._load()

    def submit(
        self,
        identity: Dict[str, Any],
        message: str,
        retry_terminal: bool = False,
    ) -> Tuple[Dict[str, Any], bool]:
        if type(retry_terminal) is not bool:
            raise ValueError("retry_terminal must be a boolean")
        normalized_identity = self._normalize_identity(identity)
        normalized_message = " ".join(message.split())
        idempotency_key = self._make_idempotency_key(
            normalized_identity, normalized_message
        )
        with self._lock:
            idempotency_generation = 0
            existing_id = self._idempotency_index.get(idempotency_key)
            if existing_id is not None:
                existing = self._jobs.get(existing_id)
                if existing is not None:
                    replaceable_terminal = existing["state"] in {
                        "failed", "cancelled", "interrupted"
                    }
                    if not retry_terminal or not replaceable_terminal:
                        return self._snapshot(existing), False
                    idempotency_generation = (
                        existing["idempotency_generation"] + 1
                    )

            job_id = self._new_job_id()
            timestamp = self._timestamp()
            record = {
                "job_id": job_id,
                "state": "queued",
                "stage": "accepted",
                "created_at": timestamp,
                "updated_at": timestamp,
                "result": None,
                "error": None,
                "identity": normalized_identity,
                "idempotency_generation": idempotency_generation,
                "idempotency_key": idempotency_key,
            }
            self._write_record(record)
            self._jobs[job_id] = record
            self._idempotency_index[idempotency_key] = job_id
            return self._snapshot(record), True

    def get(self, job_id: str, identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized_identity = self._normalize_identity(identity)
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record["identity"] != normalized_identity:
                return None
            return self._snapshot(record)

    def transition(
        self,
        job_id: str,
        state: str,
        stage: str,
        result: Optional[Any] = None,
        error: Optional[Any] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            record = self._record_for(job_id)
            current_state = record["state"]
            if state not in VALID_STATES:
                raise ValueError("invalid planning job state")
            if current_state not in ALLOWED_TRANSITIONS:
                raise ValueError("terminal planning job cannot transition")
            if state not in ALLOWED_TRANSITIONS[current_state]:
                raise ValueError(
                    "invalid planning job transition: {} -> {}".format(
                        current_state, state
                    )
                )
            candidate = deepcopy(record)
            candidate["state"] = state
            candidate["stage"] = stage
            candidate["updated_at"] = self._timestamp()
            candidate["result"], candidate["error"] = self._safe_payload(
                state, result, error
            )
            self._write_record(candidate)
            self._jobs[job_id] = candidate
            return self._snapshot(candidate)

    def cancel(
        self, job_id: str, identity: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        normalized_identity = self._normalize_identity(identity)
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record["identity"] != normalized_identity:
                return None
            if record["state"] in TERMINAL_STATES:
                return self._snapshot(record)
            candidate = deepcopy(record)
            candidate["state"] = "cancelled"
            candidate["stage"] = "finished"
            candidate["updated_at"] = self._timestamp()
            candidate["result"] = None
            candidate["error"] = None
            self._write_record(candidate)
            self._jobs[job_id] = candidate
            return self._snapshot(candidate)

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            return record is not None and record["state"] in NON_TERMINAL_STATES

    def _load(self) -> None:
        with self._lock:
            loaded_records = []
            for path in sorted(self.storage_root.glob("*.json")):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    self._validate_record(record, path)
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                loaded_records.append(
                    (record, "idempotency_generation" not in record)
                )

            legacy_records_by_key = {}
            for record, is_legacy in loaded_records:
                if is_legacy:
                    legacy_records_by_key.setdefault(
                        record["idempotency_key"], []
                    ).append(record)

            migrated_conflicts = {}
            for idempotency_key in sorted(legacy_records_by_key):
                conflict_records = legacy_records_by_key[idempotency_key]
                if len(conflict_records) < 2:
                    continue
                for record in self._migrate_legacy_conflict_records(
                    conflict_records
                ):
                    migrated_conflicts[record["job_id"]] = record

            candidates = []
            for record, _is_legacy in loaded_records:
                migrated = migrated_conflicts.get(record["job_id"])
                if migrated is not None:
                    candidates.append(migrated)
                    continue
                try:
                    candidate = self._sanitize_loaded_record(record)
                    if candidate["state"] in NON_TERMINAL_STATES:
                        interrupted = deepcopy(candidate)
                        interrupted["state"] = "interrupted"
                        interrupted["error"] = {
                            "code": "agent_restarted",
                            "message": (
                                "The Agent restarted before planning completed."
                            ),
                            "retryable": True,
                        }
                        interrupted["updated_at"] = self._timestamp()
                        self._write_record(interrupted)
                        candidate = interrupted
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                candidates.append(candidate)

            jobs = {}
            idempotency_index = {}
            for record in candidates:
                job_id = record["job_id"]
                idempotency_key = record["idempotency_key"]
                jobs[job_id] = record
                current_owner_id = idempotency_index.get(idempotency_key)
                if current_owner_id is None or self._idempotency_owner_key(
                    record
                ) > self._idempotency_owner_key(jobs[current_owner_id]):
                    idempotency_index[idempotency_key] = job_id
            self._jobs = jobs
            self._idempotency_index = idempotency_index

    def _migrate_legacy_conflict_records(self, records):
        timestamp = self._timestamp()
        safe_records = []
        # Keep the legacy marker until every conflicting payload is safe so a
        # later load can retry the whole group after any interrupted write.
        for record in sorted(records, key=lambda item: item["job_id"]):
            candidate = deepcopy(record)
            candidate["state"] = "interrupted"
            candidate["stage"] = "finished"
            candidate["updated_at"] = timestamp
            candidate["result"] = None
            candidate["error"] = {
                "code": LEGACY_CONFLICT_ERROR_CODE,
                "message": LEGACY_CONFLICT_ERROR_MESSAGE,
                "retryable": True,
            }
            self._write_record(candidate)
            safe_records.append(candidate)

        # Publish generation metadata only after no old result or error remains.
        migrated_records = []
        for record in safe_records:
            candidate = deepcopy(record)
            candidate["idempotency_generation"] = 0
            self._write_record(candidate)
            migrated_records.append(candidate)
        return migrated_records

    def _validate_record(self, record: Any, path: Path) -> None:
        if not isinstance(record, dict):
            raise ValueError("stored planning job must be an object")
        required = set(PUBLIC_SNAPSHOT_FIELDS) | {
            "identity", "idempotency_generation", "idempotency_key"
        }
        legacy_required = required - {"idempotency_generation"}
        if set(record) not in (required, legacy_required):
            raise ValueError("stored planning job has an invalid shape")
        if not isinstance(record["job_id"], str) or record["job_id"] != path.stem:
            raise ValueError("stored planning job has an invalid id")
        if record["state"] not in VALID_STATES:
            raise ValueError("stored planning job has an invalid state")
        if not isinstance(record["identity"], dict):
            raise ValueError("stored planning job has an invalid identity")
        if self._normalize_identity(record["identity"]) != record["identity"]:
            raise ValueError("stored planning job has an invalid identity")
        if not isinstance(record["idempotency_key"], str):
            raise ValueError("stored planning job has an invalid idempotency key")
        generation = record.get("idempotency_generation", 0)
        if type(generation) is not int or generation < 0:
            raise ValueError("stored planning job has an invalid generation")

    def _sanitize_loaded_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        candidate = deepcopy(record)
        candidate.setdefault("idempotency_generation", 0)
        candidate["result"], candidate["error"] = self._safe_payload(
            record["state"], record["result"], record["error"]
        )
        if candidate != record:
            self._write_record(candidate)
        return candidate

    @staticmethod
    def _idempotency_owner_key(record: Dict[str, Any]) -> Tuple[int, str]:
        return record["idempotency_generation"], record["job_id"]

    @staticmethod
    def _safe_payload(
        state: str, result: Optional[Any], error: Optional[Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if state == "completed":
            if error is not None:
                raise ValueError("completed planning job cannot contain an error")
            return PlanningJobRegistry._sanitize_result(result), None
        if state == "failed":
            if result is not None:
                raise ValueError("failed planning job cannot contain a result")
            return None, PlanningJobRegistry._sanitize_error(error)
        if state == "interrupted":
            if result is not None:
                raise ValueError("interrupted planning job cannot contain a result")
            return None, PlanningJobRegistry._sanitize_error(error)
        if result is not None or error is not None:
            raise ValueError("non-terminal planning job contains a payload")
        return None, None

    @staticmethod
    def _sanitize_result(result: Any) -> Dict[str, Any]:
        if result is None:
            raise ValueError("completed planning job requires a result")
        if not isinstance(result, dict) and hasattr(result, "as_dict"):
            result = result.as_dict()
        try:
            validated = PlanningResult.from_dict(deepcopy(result)).as_dict()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("planning result is not contract-safe") from error
        return redact_sensitive(validated)

    @staticmethod
    def _sanitize_error(error: Any) -> Dict[str, Any]:
        if not isinstance(error, dict) or set(error) != {
            "code",
            "message",
            "retryable",
        }:
            raise ValueError("planning error is not contract-safe")
        sanitized = redact_sensitive(deepcopy(error))
        if (
            not isinstance(sanitized["code"], str)
            or not sanitized["code"].strip()
            or not isinstance(sanitized["message"], str)
            or not sanitized["message"].strip()
            or type(sanitized["retryable"]) is not bool
        ):
            raise ValueError("planning error is not contract-safe")
        return {
            "code": sanitized["code"],
            "message": sanitized["message"],
            "retryable": sanitized["retryable"],
        }

    def _record_for(self, job_id: str) -> Dict[str, Any]:
        try:
            return self._jobs[job_id]
        except KeyError as error:
            raise KeyError("planning job not found") from error

    def _new_job_id(self) -> str:
        for _ in range(100):
            candidate = str(self._id_factory())
            if (
                candidate
                and Path(candidate).name == candidate
                and candidate not in {".", ".."}
                and candidate not in self._jobs
            ):
                return candidate
        raise RuntimeError("could not allocate a unique planning job id")

    def _timestamp(self) -> str:
        value = self._clock()
        if isinstance(value, datetime):
            return value.isoformat()
        if not isinstance(value, str):
            raise TypeError("clock must return a timestamp string or datetime")
        return value

    @staticmethod
    def _normalize_identity(identity: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(identity, dict):
            raise TypeError("identity must be a dict")
        missing = [field for field in IDENTITY_FIELDS if field not in identity]
        if missing:
            raise ValueError("identity is missing required fields")
        return {field: identity[field] for field in IDENTITY_FIELDS}

    @staticmethod
    def _make_idempotency_key(
        identity: Dict[str, Any], normalized_message: str
    ) -> str:
        payload = dict(identity)
        payload["message"] = normalized_message
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _write_record(self, record: Dict[str, Any]) -> None:
        path = self.storage_root / (record["job_id"] + ".json")
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(str(temporary_path), str(path))

    @staticmethod
    def _snapshot(record: Dict[str, Any]) -> Dict[str, Any]:
        return deepcopy({field: record[field] for field in PUBLIC_SNAPSHOT_FIELDS})


__all__ = ["PlanningJobRegistry"]
