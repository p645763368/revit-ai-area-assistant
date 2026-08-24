import json
from copy import deepcopy
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


def action_contract_registry():
    registry = contract_registry()
    for schema_path in (CONTRACTS / "actions").glob("*.schema.json"):
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
        whitespace_request = dict(invalid_request)
        whitespace_request["payload"] = {"message": "   "}
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
            Draft202012Validator(request_schema, registry=registry).validate(
                whitespace_request
            )
        with self.assertRaises(ValidationError):
            Draft202012Validator(response_schema, registry=registry).validate(invalid_delta)

    def test_session_action_examples_use_versioned_envelopes(self):
        registry = contract_registry()
        examples = CONTRACTS / "actions" / "examples"
        for message_type in ("request", "response"):
            schema = json.loads(
                (
                    CONTRACTS
                    / "actions"
                    / "session-{}.schema.json".format(message_type)
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema, registry=registry)
            for example_path in examples.glob(
                "session-*-{}.json".format(message_type)
            ):
                with self.subTest(example=example_path.name):
                    example = json.loads(example_path.read_text(encoding="utf-8"))
                    validator.validate(example)
                    self.assertEqual(example["contract_version"], "1.0")
                    self.assertEqual(example["message_type"], message_type)

    def test_planning_action_examples_require_clickable_recommendations(self):
        registry = contract_registry()
        for message_type in ("request", "response"):
            schema = json.loads(
                (CONTRACTS / "actions" / "planning-{}.schema.json".format(message_type)).read_text(encoding="utf-8")
            )
            example = json.loads(
                (CONTRACTS / "actions" / "examples" / "planning-{}.json".format(message_type)).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema, registry=registry).validate(example)
        options = json.loads(
            (CONTRACTS / "actions" / "examples" / "planning-response.json").read_text(encoding="utf-8")
        )["payload"]["options"]
        self.assertGreaterEqual(len(options), 2)
        self.assertLessEqual(len(options), 4)
        self.assertEqual(sum(option["recommended"] for option in options), 1)

    def test_planning_job_action_examples_cover_the_public_lifecycle(self):
        """Fail if an API action loses a required request or terminal snapshot."""
        registry = action_contract_registry()
        actions = CONTRACTS / "actions"
        examples = actions / "examples"
        for action in ("submit", "cancel"):
            with self.subTest(action=action):
                schema = json.loads(
                    (actions / "planning-job-{}.schema.json".format(action)).read_text(
                        encoding="utf-8"
                    )
                )
                example = json.loads(
                    (examples / "planning-job-{}.json".format(action)).read_text(
                        encoding="utf-8"
                    )
                )
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema, registry=registry).validate(example)

        status_examples = json.loads(
            (examples / "planning-job-status.json").read_text(encoding="utf-8")
        )
        status_schema = json.loads(
            (actions / "planning-job-status.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(status_schema)
        status_validator = Draft202012Validator(status_schema, registry=registry)
        for example in status_examples:
            with self.subTest(status_state=example["payload"]["state"]):
                status_validator.validate(example)
        states = {example["payload"]["state"] for example in status_examples}
        self.assertTrue(states & {"queued", "running"})
        self.assertTrue(
            {"completed", "failed", "cancelled", "interrupted"}.issubset(states)
        )

    def test_planning_job_submit_response_keeps_accepted_status_for_deduplicated_terminal_job(self):
        """Fail if submit responses adopt the poll endpoint's terminal status."""
        registry = action_contract_registry()
        submit_schema = json.loads(
            (
                CONTRACTS / "actions" / "planning-job-submit.schema.json"
            ).read_text(encoding="utf-8")
        )
        status_examples = json.loads(
            (
                CONTRACTS / "actions" / "examples" / "planning-job-status.json"
            ).read_text(encoding="utf-8")
        )
        deduplicated_terminal = next(
            example
            for example in status_examples
            if example["payload"]["state"] == "completed"
        )
        deduplicated_terminal["status"] = "accepted"

        Draft202012Validator(submit_schema, registry=registry).validate(
            deduplicated_terminal
        )

    def test_planning_job_poll_query_requires_each_identity_field(self):
        """Fail if the public poll query becomes incomplete or permissive."""
        actions = CONTRACTS / "actions"
        example = json.loads(
            (actions / "examples" / "planning-job-status-query.json").read_text(
                encoding="utf-8"
            )
        )
        schema = json.loads(
            (actions / "planning-job-status-query.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validator = Draft202012Validator(schema)
        validator.validate(example)

        invalid_values = {
            "project_directory": "",
            "panel_instance_id": "",
            "generation": -1,
            "context_id": "",
            "document_fingerprint": "",
            "session_id": "",
        }
        for field, invalid_value in invalid_values.items():
            with self.subTest(missing=field):
                missing = deepcopy(example)
                del missing[field]
                with self.assertRaises(ValidationError):
                    validator.validate(missing)
            with self.subTest(invalid=field):
                invalid = deepcopy(example)
                invalid[field] = invalid_value
                with self.assertRaises(ValidationError):
                    validator.validate(invalid)

        unexpected = deepcopy(example)
        unexpected["unexpected"] = "value"
        with self.assertRaises(ValidationError):
            validator.validate(unexpected)

    def test_planning_job_status_rejects_impossible_state_stage_combinations(self):
        """Fail if terminal or queued snapshots advertise an impossible stage."""
        registry = action_contract_registry()
        actions = CONTRACTS / "actions"
        examples = json.loads(
            (actions / "examples" / "planning-job-status.json").read_text(
                encoding="utf-8"
            )
        )
        schema = json.loads(
            (actions / "planning-job-status.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validator = Draft202012Validator(schema, registry=registry)
        completed = next(
            example for example in examples if example["payload"]["state"] == "completed"
        )
        running = next(
            example for example in examples if example["payload"]["state"] == "running"
        )

        terminal_accepted = deepcopy(completed)
        terminal_accepted["payload"]["stage"] = "accepted"
        queued_finished = deepcopy(running)
        queued_finished["payload"]["state"] = "queued"
        queued_finished["payload"]["stage"] = "finished"
        for snapshot in (terminal_accepted, queued_finished):
            with self.subTest(
                state=snapshot["payload"]["state"], stage=snapshot["payload"]["stage"]
            ):
                with self.assertRaises(ValidationError):
                    validator.validate(snapshot)


if __name__ == "__main__":
    unittest.main()
