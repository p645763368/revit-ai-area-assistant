import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "v1"


class SharedContractExamplesTests(unittest.TestCase):
    def test_each_public_message_type_has_a_versioned_schema_and_example(self):
        for message_type in ("request", "response", "state", "error"):
            with self.subTest(message_type=message_type):
                schema = json.loads((CONTRACTS / f"{message_type}.schema.json").read_text(encoding="utf-8"))
                example = json.loads((CONTRACTS / "examples" / f"{message_type}.json").read_text(encoding="utf-8"))

                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(example)


if __name__ == "__main__":
    unittest.main()
