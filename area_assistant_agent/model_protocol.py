"""Normalize provider chat-completion response shapes."""

import json
from typing import Any, Dict


class ProtocolShapeError(Exception):
    def __init__(self, shape: Dict[str, Any]):
        super().__init__("provider response shape is incompatible")
        self.shape = shape


def summarize_chat_completion_shape(payload: Any) -> Dict[str, Any]:
    """Return structural metadata without copying provider content."""
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}
    choices = payload.get("choices")
    summary: Dict[str, Any] = {
        "payload_type": "dict",
        "keys": sorted(str(key) for key in payload.keys()),
        "choices_type": type(choices).__name__,
    }
    if isinstance(choices, list):
        summary["choices_length"] = len(choices)
        if choices and isinstance(choices[0], dict):
            summary["choice_keys"] = sorted(str(key) for key in choices[0].keys())
            message = choices[0].get("message")
            summary["message_type"] = type(message).__name__
            if isinstance(message, dict):
                summary["message_keys"] = sorted(str(key) for key in message.keys())
                content = message.get("content")
                summary["content_type"] = type(content).__name__
    return summary


def _shape_error(payload: Any) -> ProtocolShapeError:
    return ProtocolShapeError(summarize_chat_completion_shape(payload))


def _first_message(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise _shape_error(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _shape_error(payload)
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise _shape_error(payload)
    return message


def _normalize_content(content: Any) -> Any:
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        return "".join(pieces) if pieces else None
    return None


def _normalize_arguments(arguments: Any) -> Dict[str, Any] | None:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _normalize_calls(message: Dict[str, Any]) -> list[Dict[str, Any]] | None:
    raw_calls = message.get("tool_calls")
    if raw_calls is not None:
        if not isinstance(raw_calls, list):
            return None
        calls = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                return None
            function = raw_call.get("function")
            if not isinstance(function, dict):
                return None
            call_id = raw_call.get("id")
            name = function.get("name")
            arguments = _normalize_arguments(function.get("arguments"))
            if not isinstance(call_id, str) or not isinstance(name, str) or arguments is None:
                return None
            calls.append({"id": call_id, "name": name, "arguments": arguments})
        return calls

    legacy = message.get("function_call")
    if legacy is None:
        return []
    if not isinstance(legacy, dict) or not isinstance(legacy.get("name"), str):
        return None
    arguments = _normalize_arguments(legacy.get("arguments"))
    if arguments is None:
        return None
    return [{"id": "legacy-call-0", "name": legacy["name"], "arguments": arguments}]


def normalize_chat_completion(payload: Any) -> Dict[str, Any]:
    message = _first_message(payload)
    content = _normalize_content(message.get("content"))
    calls = _normalize_calls(message)
    if calls is None or (content is None and not calls):
        raise _shape_error(payload)
    return {"content": content, "tool_calls": calls}
