import unittest
import json

from area_assistant_agent.model_protocol import (
    ProtocolShapeError,
    normalize_chat_completion,
    summarize_chat_completion_shape,
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

    def test_rejects_blank_tool_call_identifiers(self):
        for call_id, name in [
            ("", "inspect_revit_model"),
            ("   ", "inspect_revit_model"),
            ("call-1", ""),
            ("call-1", "   "),
        ]:
            payload = {"choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": call_id, "type": "function",
                    "function": {"name": name, "arguments": {}}}],
            }}]}
            with self.subTest(call_id=call_id, name=name):
                with self.assertRaises(ProtocolShapeError):
                    normalize_chat_completion(payload)

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

    def test_shape_summary_is_value_free_and_allowlists_keys(self):
        payload = {
            "id": "SECRET_KEY",
            "choices": [{"message": {
                "content": "SECRET_KEY",
                "reasoning_content": "SECRET_KEY",
                "tool_calls": [{"function": {
                    "name": "inspect_secret",
                    "arguments": {"revit_path": "C:\\Secret.rvt", "token": "token-value"},
                }}],
                "authorization": "token-value",
            }}],
            "fingerprint": "SECRET_KEY",
        }

        summary = summarize_chat_completion_shape(payload)
        serialized = json.dumps(summary, sort_keys=True)

        for forbidden in ("SECRET_KEY", "C:\\Secret.rvt", "inspect_secret", "token-value"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(summary["top_level"]["keys"], ["choices", "id"])
        self.assertEqual(summary["choices"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
