"""Small OpenAI-compatible streaming client used by the local Agent."""

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from .model_protocol import (
    ProtocolShapeError,
    normalize_chat_completion,
    summarize_chat_completion_shape,
)


class ModelApiError(Exception):
    def __init__(
        self,
        code,
        message,
        retryable=True,
        diagnostic_id=None,
        diagnostic=None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.diagnostic_id = diagnostic_id
        self.diagnostic = diagnostic


def _protocol_error(shape):
    return ModelApiError(
        "model_protocol_error",
        "Model API returned an incompatible response.",
        retryable=True,
        diagnostic_id=uuid.uuid4().hex,
        diagnostic=shape,
    )


def _protocol_error_for_payload(payload):
    return _protocol_error(summarize_chat_completion_shape(payload))


_USAGE_EVENT_KEYS = {
    "id",
    "object",
    "created",
    "model",
    "system_fingerprint",
    "choices",
    "usage",
}
_USAGE_REQUIRED_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens"}
_USAGE_OPTIONAL_INTEGER_KEYS = {
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
}
_USAGE_DETAIL_KEYS = {
    "prompt_tokens_details": {"cached_tokens", "audio_tokens"},
    "completion_tokens_details": {
        "reasoning_tokens",
        "audio_tokens",
        "accepted_prediction_tokens",
        "rejected_prediction_tokens",
    },
}


def _is_nonnegative_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_usage_event(event):
    if set(event) - _USAGE_EVENT_KEYS or event.get("choices") != []:
        return False
    usage = event.get("usage")
    if not isinstance(usage, dict) or not _USAGE_REQUIRED_KEYS.issubset(usage):
        return False
    allowed_usage_keys = (
        _USAGE_REQUIRED_KEYS
        | _USAGE_OPTIONAL_INTEGER_KEYS
        | set(_USAGE_DETAIL_KEYS)
    )
    if set(usage) - allowed_usage_keys:
        return False
    integer_keys = _USAGE_REQUIRED_KEYS | _USAGE_OPTIONAL_INTEGER_KEYS
    if any(
        key in usage and not _is_nonnegative_integer(usage[key])
        for key in integer_keys
    ):
        return False
    for key, allowed_detail_keys in _USAGE_DETAIL_KEYS.items():
        if key not in usage:
            continue
        details = usage[key]
        if (
            not isinstance(details, dict)
            or set(details) - allowed_detail_keys
            or any(not _is_nonnegative_integer(value) for value in details.values())
        ):
            return False
    for key in ("id", "object", "model"):
        if key in event and not isinstance(event[key], str):
            return False
    if "created" in event and not _is_nonnegative_integer(event["created"]):
        return False
    if "system_fingerprint" in event and not (
        event["system_fingerprint"] is None
        or isinstance(event["system_fingerprint"], str)
    ):
        return False
    return True


def _normalize_sse_event(event):
    """Return a validated text delta and termination flag for one SSE event."""
    if not isinstance(event, dict):
        raise _protocol_error_for_payload(event)
    choices = event.get("choices")
    if choices == [] or "usage" in event:
        if not _valid_usage_event(event):
            raise _protocol_error_for_payload(event)
        return None, False
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _protocol_error_for_payload(event)
    choice = choices[0]
    terminated = choice.get("finish_reason") is not None
    delta = choice.get("delta")
    if delta is None and terminated:
        return None, True
    if not isinstance(delta, dict):
        raise _protocol_error_for_payload(event)
    if set(delta) - {"role", "content", "reasoning_content"}:
        raise _protocol_error_for_payload(event)
    if "role" in delta and not isinstance(delta["role"], str):
        raise _protocol_error_for_payload(event)
    content = delta.get("content")
    if content is None:
        if "reasoning_content" in delta:
            raise _protocol_error_for_payload(event)
        if terminated or "role" in delta:
            return None, terminated
        raise _protocol_error_for_payload(event)
    if not isinstance(content, str):
        raise _protocol_error_for_payload(event)
    return content, terminated


class OpenAICompatibleClient:
    def __init__(self, config):
        self._config = config

    def stream_reply(self, message):
        if not self._config.api_key or not self._config.model:
            raise ModelApiError(
                "model_not_configured",
                "Model API credentials or model name are not configured.",
                retryable=True,
            )
        body = json.dumps(
            {
                "model": self._config.model,
                "messages": [{"role": "user", "content": message}],
                "stream": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self._config.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + self._config.api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            terminated = False
            received_content = False
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                for raw_line in response:
                    try:
                        line = raw_line.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        raise _protocol_error_for_payload(None) from None
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        if not terminated or not received_content:
                            raise _protocol_error_for_payload(None)
                        return
                    try:
                        event = json.loads(data)
                    except ValueError:
                        raise _protocol_error_for_payload(None) from None
                    content, event_terminated = _normalize_sse_event(event)
                    terminated = terminated or event_terminated
                    if content:
                        received_content = True
                        yield content
            if not terminated or not received_content:
                raise _protocol_error_for_payload(None)
        except HTTPError as exc:
            raise ModelApiError(
                "model_http_error",
                "Model API request failed with HTTP status {}.".format(exc.code),
                retryable=True,
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ModelApiError(
                "model_timeout", "Model API request timed out.", retryable=True
            ) from exc
        except URLError as exc:
            raise ModelApiError(
                "model_unavailable", "Model API is unavailable.", retryable=True
            ) from exc

    def planning_turn(self, messages, tools, response_format=None):
        """Return one normalized, non-streaming assistant/tool-call turn."""
        if not self._config.api_key or not self._config.model:
            raise ModelApiError(
                "model_not_configured",
                "Model API credentials or model name are not configured.",
                retryable=True,
            )
        body_value = {
            "model": self._config.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
        }
        if response_format is not None:
            body_value["response_format"] = response_format
        body = json.dumps(body_value, ensure_ascii=False).encode("utf-8")
        request = Request(
            self._config.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + self._config.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                try:
                    decoded_body = response.read().decode("utf-8")
                except UnicodeDecodeError:
                    raise _protocol_error_for_payload(None) from None
            try:
                payload = json.loads(decoded_body)
            except (TypeError, ValueError):
                raise _protocol_error_for_payload(None) from None
            try:
                return normalize_chat_completion(payload)
            except ProtocolShapeError as error:
                raise _protocol_error(error.shape) from None
        except ModelApiError:
            raise
        except HTTPError as exc:
            raise ModelApiError(
                "model_http_error",
                "Model API request failed with HTTP status {}.".format(exc.code),
                retryable=True,
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ModelApiError(
                "model_timeout", "Model API request timed out.", retryable=True
            ) from exc
        except URLError as exc:
            raise ModelApiError(
                "model_unavailable", "Model API is unavailable.", retryable=True
            ) from exc
