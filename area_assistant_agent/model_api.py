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
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        event = json.loads(data)
                    except ValueError:
                        raise _protocol_error_for_payload(None) from None
                    if not isinstance(event, dict):
                        raise _protocol_error_for_payload(event)
                    choices = event.get("choices")
                    if choices == [] or (choices is None and "usage" in event):
                        continue
                    if not isinstance(choices, list) or not isinstance(choices[0], dict):
                        raise _protocol_error_for_payload(event)
                    choice = choices[0]
                    if choice.get("finish_reason") is not None:
                        terminated = True
                    delta = choice.get("delta")
                    if delta is None and choice.get("finish_reason") is not None:
                        continue
                    if not isinstance(delta, dict):
                        raise _protocol_error_for_payload(event)
                    content = delta.get("content")
                    if content is None:
                        continue
                    if not isinstance(content, str):
                        raise _protocol_error_for_payload(event)
                    if content:
                        yield content
            if not terminated:
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
                decoded_body = response.read().decode("utf-8")
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
