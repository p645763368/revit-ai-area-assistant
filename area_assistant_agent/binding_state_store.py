"""Minimal Agent-owned runtime latch for one Revit process binding."""

import json
import os
from pathlib import Path
import tempfile
from typing import Optional


class BindingStateStore:
    def __init__(self, root: Optional[Path] = None):
        local_app_data = os.environ.get("LOCALAPPDATA", tempfile.gettempdir())
        self._root = root or Path(local_app_data) / "RevitAIAreaAssistant" / "runtime"

    def load(self, instance_id: str) -> Optional[dict]:
        path = self._path(instance_id)
        if not path.is_file():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"bound_document": None, "pause_reason": "document_observation_failed"}
        if state.get("instance_id") != instance_id:
            return {"bound_document": None, "pause_reason": "document_observation_failed"}
        return state

    def save(self, instance_id: str, bound_document: dict, pause_reason: Optional[str]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(instance_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "instance_id": instance_id,
                    "bound_document": bound_document,
                    "pause_reason": pause_reason,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(str(temporary), str(path))

    def _path(self, instance_id: str) -> Path:
        safe_instance_id = "".join(
            character for character in instance_id if character.isalnum() or character in "-_"
        )
        if not safe_instance_id:
            safe_instance_id = "invalid"
        return self._root / (safe_instance_id + ".json")
