import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from area_assistant_agent.config import AgentConfig
from area_assistant_agent.model_api import OpenAICompatibleClient


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
                [{"type": "function", "function": {"name": "inspect_revit_model"}}],
            )

            self.assertEqual(result["tool_calls"][0]["name"], "inspect_revit_model")
            self.assertEqual(result["tool_calls"][0]["arguments"], {"query": "levels"})
            self.assertFalse(server.received["stream"])
            self.assertEqual(server.received["tool_choice"], "auto")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
