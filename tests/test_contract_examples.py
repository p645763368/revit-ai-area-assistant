import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "v1"


def contract_registry():
    registry = Registry()
    for schema_path in CONTRACTS.glob("*.schema.json"):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry


class SharedContractExamplesTests(unittest.TestCase):
    def test_each_public_message_type_has_a_versioned_schema_and_example(self):
        for message_type in ("request", "response", "state", "error"):
            with self.subTest(message_type=message_type):
                schema = json.loads((CONTRACTS / f"{message_type}.schema.json").read_text(encoding="utf-8"))
                example = json.loads((CONTRACTS / "examples" / f"{message_type}.json").read_text(encoding="utf-8"))

                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(example)

    def test_every_feature_example_conforms_to_its_public_envelope(self):
        feature_schemas = {
            "chat-request.json": "chat-request.schema.json",
            "chat-delta-response.json": "chat-response.schema.json",
            "chat-completed-response.json": "chat-response.schema.json",
        }
        registry = contract_registry()
        for example_path in (CONTRACTS / "examples").glob("*.json"):
            with self.subTest(example=example_path.name):
                example = json.loads(example_path.read_text(encoding="utf-8"))
                schema_name = feature_schemas.get(
                    example_path.name, "{}.schema.json".format(example["message_type"])
                )
                schema = json.loads(
                    (CONTRACTS / schema_name).read_text(encoding="utf-8")
                )
                Draft202012Validator(schema, registry=registry).validate(example)

    def test_chat_feature_schemas_reject_invalid_payloads(self):
        registry = contract_registry()
        request_schema = json.loads(
            (CONTRACTS / "chat-request.schema.json").read_text(encoding="utf-8")
        )
        response_schema = json.loads(
            (CONTRACTS / "chat-response.schema.json").read_text(encoding="utf-8")
        )
        invalid_request = {
            "contract_version": "1.0",
            "message_type": "request",
            "request_id": "req-invalid",
            "action": "chat.stream",
            "payload": {},
        }
        invalid_delta = {
            "contract_version": "1.0",
            "message_type": "response",
            "request_id": "req-invalid",
            "status": "accepted",
            "payload": {"delta": 42},
        }

        with self.assertRaises(ValidationError):
            Draft202012Validator(request_schema, registry=registry).validate(invalid_request)
        with self.assertRaises(ValidationError):
            Draft202012Validator(response_schema, registry=registry).validate(invalid_delta)


if __name__ == "__main__":
    unittest.main()
