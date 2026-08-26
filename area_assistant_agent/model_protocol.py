"""Normalize provider chat-completion response shapes."""

import json
from typing import Any, Dict


_ALLOWED_SHAPE_KEYS = frozenset(
    {
        "id",
        "object",
        "choices",
        "message",
        "content",
        "tool_calls",
        "function_call",
        "function",
        "arguments",
        "finish_reason",
        "usage",
        "reasoning_content",
    }
)


class ProtocolShapeError(Exception):
    def __init__(self, shape: Dict[str, Any]):
        super().__init__("provider response shape is incompatible")
        self.shape = shape


def summarize_chat_completion_shape(payload: Any) -> Dict[str, Any]:
    """Return an allowlisted structural summary without provider values."""
    summary: Dict[str, Any] = {"top_level": _summarize_value(payload)}
    if isinstance(payload, dict) and "choices" in payload:
        summary["choices"] = _summarize_value(payload["choices"])
    return summary


def _summarize_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        allowed_keys = sorted(
            key for key in value if isinstance(key, str) and key in _ALLOWED_SHAPE_KEYS
        )
        summary: Dict[str, Any] = {
            "type": "dict",
            "keys": allowed_keys,
            "unknown_key_count": len(value) - len(allowed_keys),
        }
        fields = {key: _summarize_value(value[key]) for key in allowed_keys}
        if fields:
            summary["fields"] = fields
        return summary
    if isinstance(value, list):
        item_type_counts: Dict[str, int] = {}
        for item in value:
            item_type = type(item).__name__
            item_type_counts[item_type] = item_type_counts.get(item_type, 0) + 1
        return {
            "type": "list",
            "count": len(value),
            "item_type_counts": item_type_counts,
            "first_item": _summarize_value(value[0]) if value else None,
        }
    return {"type": type(value).__name__}


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


def _supported_content(content: Any) -> bool:
    if content is None or isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    return all(
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        for block in content
    )


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
            if raw_call.get("type") != "function":
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
    raw_content = message.get("content")
    if not _supported_content(raw_content):
        raise _shape_error(payload)
    content = _normalize_content(raw_content)
    calls = _normalize_calls(message)
    if calls is None or (content is None and not calls):
        raise _shape_error(payload)
    return {"content": content, "tool_calls": calls}
