import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.error import HTTPError, URLError
import unittest
from unittest.mock import patch

from area_assistant_agent.config import AgentConfig
from area_assistant_agent.model_api import ModelApiError, OpenAICompatibleClient
from area_assistant_agent.planning import PLANNING_RESPONSE_FORMAT, TOOL_DEFINITIONS


class _PlanningHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.server.received = json.loads(self.rfile.read(length))
        response = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "inspect_revit_model",
                                    "arguments": '{"query":"levels"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class ModelPlanningApiTests(unittest.TestCase):
    def setUp(self):
        self.client = OpenAICompatibleClient(
            AgentConfig(
                host="127.0.0.1",
                port=0,
                base_url="http://127.0.0.1:1/v1",
                api_key="unit-test",
                model="test-model",
                timeout_seconds=1,
            )
        )

    def test_non_streaming_tool_call_is_normalized_for_planning_loop(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _PlanningHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = AgentConfig(
                host="127.0.0.1",
                port=0,
                base_url="http://127.0.0.1:{}/v1".format(server.server_port),
                api_key="unit-test",
                model="test-model",
                timeout_seconds=1,
            )
            client = OpenAICompatibleClient(config)

            result = client.planning_turn(
                [{"role": "user", "content": "scan"}],
                TOOL_DEFINITIONS,
                PLANNING_RESPONSE_FORMAT,
            )

            self.assertEqual(result["tool_calls"][0]["name"], "inspect_revit_model")
            self.assertEqual(result["tool_calls"][0]["arguments"], {"query": "levels"})
            self.assertFalse(server.received["stream"])
            self.assertEqual(server.received["tool_choice"], "auto")
            self.assertEqual(server.received["response_format"]["type"], "json_schema")
            schema = server.received["response_format"]["json_schema"]["schema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["properties"]["options"]["minItems"], 2)
            self.assertEqual(schema["properties"]["options"]["maxItems"], 4)
            self.assertTrue(server.received["tools"][0]["function"]["strict"])
            self.assertFalse(
                server.received["tools"][0]["function"]["parameters"]["additionalProperties"]
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_planning_turn_uses_normalizer_for_content_blocks_and_object_arguments(self):
        response = _Response(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "first"},
                                {"type": "text", "text": "second"},
                            ],
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_revit_model",
                                        "arguments": {"query": "levels"},
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        with patch("area_assistant_agent.model_api.urlopen", return_value=response):
            result = self.client.planning_turn([], [])

        self.assertEqual(result["content"], "firstsecond")
        self.assertEqual(result["tool_calls"][0]["arguments"], {"query": "levels"})

    def test_planning_protocol_failure_has_value_free_diagnostic(self):
        response = _Response(
            {
                "choices": [
                    {
                        "message": {
                            "content": {"secret": "secret-delta-content"},
                            "reasoning_content": "secret-reasoning",
                        }
                    }
                ]
            }
        )

        with patch("area_assistant_agent.model_api.urlopen", return_value=response):
            with self.assertRaises(ModelApiError) as caught:
                self.client.planning_turn([], [])

        error = caught.exception
        self.assertEqual(error.code, "model_protocol_error")
        self.assertEqual(str(error), "Model API returned an incompatible response.")
        self.assertIsNotNone(error.diagnostic_id)
        self.assertNotIn("secret", json.dumps(error.diagnostic))

    def test_planning_http_status_is_classified_without_protocol_diagnostic(self):
        failure = HTTPError("http://provider", 429, "secret status", {}, None)
        with patch("area_assistant_agent.model_api.urlopen", side_effect=failure):
            with self.assertRaises(ModelApiError) as caught:
                self.client.planning_turn([], [])

        self.assertEqual(caught.exception.code, "model_http_error")
        self.assertEqual(str(caught.exception), "Model API request failed with HTTP status 429.")
        self.assertIsNone(caught.exception.diagnostic_id)

    def test_planning_socket_timeout_is_classified(self):
        with patch(
            "area_assistant_agent.model_api.urlopen",
            side_effect=socket.timeout("secret timeout"),
        ):
            with self.assertRaises(ModelApiError) as caught:
                self.client.planning_turn([], [])

        self.assertEqual(caught.exception.code, "model_timeout")
        self.assertEqual(str(caught.exception), "Model API request timed out.")

    def test_planning_url_error_is_classified(self):
        with patch(
            "area_assistant_agent.model_api.urlopen",
            side_effect=URLError("secret unavailable"),
        ):
            with self.assertRaises(ModelApiError) as caught:
                self.client.planning_turn([], [])

        self.assertEqual(caught.exception.code, "model_unavailable")
        self.assertEqual(str(caught.exception), "Model API is unavailable.")

    def test_stream_reply_accepts_valid_sse_and_rejects_malformed_event_safely(self):
        valid = _StreamingResponse(
            [
                {"choices": [{"delta": {"content": "hello"}}]},
                {"choices": [{"finish_reason": "stop"}]},
            ]
        )
        with patch("area_assistant_agent.model_api.urlopen", return_value=valid):
            self.assertEqual(list(self.client.stream_reply("message")), ["hello"])

        malformed = _StreamingResponse(
            [
                {
                    "choices": [
                        {"delta": {"content": ["secret-delta-content"]}}
                    ],
                    "secret_unknown": "secret-provider-value",
                }
            ]
        )
        with patch("area_assistant_agent.model_api.urlopen", return_value=malformed):
            with self.assertRaises(ModelApiError) as caught:
                list(self.client.stream_reply("message"))

        error = caught.exception
        self.assertEqual(error.code, "model_protocol_error")
        self.assertEqual(str(error), "Model API returned an incompatible response.")
        serialized = json.dumps(error.diagnostic)
        self.assertNotIn("secret", serialized)
        self.assertIn('"keys"', serialized)
        self.assertIn('"type"', serialized)

    def test_planning_invalid_utf8_is_a_safe_protocol_error(self):
        with patch(
            "area_assistant_agent.model_api.urlopen",
            return_value=_RawResponse(b"\xffsecret"),
        ):
            with self.assertRaises(ModelApiError) as caught:
                self.client.planning_turn([], [])

        self.assertEqual(caught.exception.code, "model_protocol_error")
        self.assertEqual(
            str(caught.exception), "Model API returned an incompatible response."
        )
        self.assertNotIn("secret", json.dumps(caught.exception.diagnostic))

    def test_stream_invalid_utf8_is_a_safe_protocol_error(self):
        with patch(
            "area_assistant_agent.model_api.urlopen",
            return_value=_RawStreamingResponse([b"data: \xffsecret\n\n"]),
        ):
            with self.assertRaises(ModelApiError) as caught:
                list(self.client.stream_reply("message"))

        self.assertEqual(caught.exception.code, "model_protocol_error")
        self.assertEqual(
            str(caught.exception), "Model API returned an incompatible response."
        )
        self.assertNotIn("secret", json.dumps(caught.exception.diagnostic))

    def test_stream_reasoning_only_or_unknown_delta_fails_closed_without_leaking_values(self):
        malformed_events = (
            {
                "choices": [
                    {"delta": {"reasoning_content": "secret-reasoning-sentinel"}}
                ]
            },
            {"choices": [{"delta": {"unknown": "secret-unknown-sentinel"}}]},
        )
        for event in malformed_events:
            with self.subTest(event_keys=sorted(event["choices"][0]["delta"])):
                with patch(
                    "area_assistant_agent.model_api.urlopen",
                    return_value=_StreamingResponse(
                        [event, {"choices": [{"finish_reason": "stop"}]}]
                    ),
                ):
                    with self.assertRaises(ModelApiError) as caught:
                        list(self.client.stream_reply("message"))

                serialized = json.dumps(caught.exception.diagnostic)
                self.assertEqual(caught.exception.code, "model_protocol_error")
                self.assertNotIn("secret", serialized)


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._body


class _RawResponse(_Response):
    def __init__(self, body):
        self._body = body


class _StreamingResponse:
    def __init__(self, payloads):
        self._lines = [
            ("data: " + json.dumps(payload) + "\n\n").encode("utf-8")
            for payload in payloads
        ] + [b"data: [DONE]\n\n"]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        return iter(self._lines)


class _RawStreamingResponse(_StreamingResponse):
    def __init__(self, lines):
        self._lines = lines


if __name__ == "__main__":
    unittest.main()
