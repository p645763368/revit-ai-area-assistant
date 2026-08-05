"""Small OpenAI-compatible streaming client used by the local Agent."""

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelApiError(Exception):
    def __init__(self, code, message, retryable=True):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _protocol_error():
    return ModelApiError(
        "model_protocol_error",
        "Model API returned an incompatible streaming response.",
        retryable=True,
    )


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
                    except ValueError as exc:
                        raise _protocol_error() from exc
                    if not isinstance(event, dict):
                        raise _protocol_error()
                    choices = event.get("choices")
                    if choices == [] or (choices is None and "usage" in event):
                        continue
                    if not isinstance(choices, list) or not isinstance(choices[0], dict):
                        raise _protocol_error()
                    choice = choices[0]
                    delta = choice.get("delta")
                    if delta is None and choice.get("finish_reason") is not None:
                        continue
                    if not isinstance(delta, dict):
                        raise _protocol_error()
                    content = delta.get("content")
                    if content is None:
                        continue
                    if not isinstance(content, str):
                        raise _protocol_error()
                    if content:
                        yield content
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
