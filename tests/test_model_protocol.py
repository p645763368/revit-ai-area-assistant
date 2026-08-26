import unittest

from area_assistant_agent.model_protocol import (
    ProtocolShapeError,
    normalize_chat_completion,
)


class ModelProtocolTests(unittest.TestCase):
    def test_normalizes_standard_content(self):
        payload = {"choices": [{"message": {"content": '{"ok":true}'}}]}
        self.assertEqual(
            normalize_chat_completion(payload),
            {"content": '{"ok":true}', "tool_calls": []},
        )

    def test_normalizes_text_content_blocks(self):
        payload = {"choices": [{"message": {"content": [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]}}]}
        self.assertEqual(
            normalize_chat_completion(payload)["content"], "firstsecond"
        )

    def test_normalizes_string_and_object_tool_arguments(self):
        for arguments in ('{"query":"levels"}', {"query": "levels"}):
            payload = {"choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function",
                    "function": {"name": "inspect_revit_model", "arguments": arguments}}],
            }}]}
            self.assertEqual(
                normalize_chat_completion(payload)["tool_calls"][0]["arguments"],
                {"query": "levels"},
            )

    def test_normalizes_legacy_function_call(self):
        payload = {"choices": [{"message": {"content": None, "function_call": {
            "name": "inspect_revit_model", "arguments": '{"query":"levels"}'
        }}}]}
        call = normalize_chat_completion(payload)["tool_calls"][0]
        self.assertEqual(call["id"], "legacy-call-0")

    def test_rejects_reasoning_only_or_unknown_content(self):
        invalid = [
            {"choices": [{"message": {"content": None, "reasoning_content": "secret"}}]},
            {"choices": [{"message": {"content": [{"type": "image", "url": "secret"}]}}]},
        ]
        for payload in invalid:
            with self.assertRaises(ProtocolShapeError):
                normalize_chat_completion(payload)

    def test_rejects_unsupported_content_mixed_with_text(self):
        payload = {"choices": [{"message": {"content": [
            {"type": "text", "text": "visible"},
            {"type": "image", "url": "provider-secret"},
        ]}}]}
        with self.assertRaises(ProtocolShapeError):
            normalize_chat_completion(payload)

    def test_rejects_unsupported_content_with_valid_tool_call(self):
        payload = {"choices": [{"message": {
            "content": [{"type": "image", "url": "provider-secret"}],
            "tool_calls": [{"id": "call-1", "type": "function",
                "function": {"name": "inspect", "arguments": {}}}],
        }}]}
        with self.assertRaises(ProtocolShapeError):
            normalize_chat_completion(payload)

    def test_rejects_unsupported_scalar_content_with_valid_call(self):
        payload = {"choices": [{"message": {
            "content": {"provider_secret": "do-not-copy"},
            "tool_calls": [{"id": "call-1", "type": "function",
                "function": {"name": "inspect", "arguments": {}}}],
        }}]}
        with self.assertRaises(ProtocolShapeError):
            normalize_chat_completion(payload)

    def test_rejects_non_function_tool_call_type(self):
        payload = {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "call-1", "type": "other",
                "function": {"name": "inspect", "arguments": {}}}],
        }}]}
        with self.assertRaises(ProtocolShapeError):
            normalize_chat_completion(payload)

    def test_protocol_error_shape_excludes_provider_values(self):
        sentinel = "provider-secret-value"
        payload = {"choices": [{"message": {
            "content": {"secret": sentinel},
            "reasoning_content": sentinel,
        }}]}
        with self.assertRaises(ProtocolShapeError) as context:
            normalize_chat_completion(payload)
        self.assertNotIn(sentinel, repr(context.exception.shape))
        self.assertEqual(context.exception.shape["content_type"], "dict")


if __name__ == "__main__":
    unittest.main()
