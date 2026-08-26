# Model Protocol Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed DeepSeek-compatible model protocol gateway that normalizes provider responses, constrains tool/final JSON output, emits value-free diagnostics, and never automatically repeats a potentially billable request.

**Architecture:** Split provider-envelope parsing and diagnostic summarization out of `model_api.py`, leaving the planning agent dependent on one `{content, tool_calls}` turn shape. The existing durable job remains the retry/billing authority; model errors carry a safe diagnostic object to the server, which persists it inside the canonical session directory before publishing one terminal error.

**Tech Stack:** Python 3.9+, standard-library `urllib`, `json`, `unittest`, JSON Schema Draft 2020-12, pyRevit/IronPython-compatible public contracts.

**Spec:** `docs/superpowers/specs/2026-08-26-model-protocol-gateway-design.md`

## Global Constraints

- Production base URL is `https://api.deepseek.com` and production model is `deepseek-v4-flash`.
- Read credentials only from `AI_AREA_ASSISTANT_API_KEY`; never print, persist, fixture, or commit them.
- Do not store prompts, conversations, model content/reasoning, Revit evidence, tool names/values, paths, screenshots, fingerprints, authorization headers, or raw provider bodies in protocol diagnostics.
- Unknown provider shapes fail closed as `model_protocol_error`.
- No transport, protocol, or validation failure automatically sends another model request.
- Planning remains read-only and exposes no Revit write tools.
- Every live provider request and every live Revit Scan and Plan requires separate user authorization.
- Preserve unrelated launcher worktree changes and commit only files named by the current task.

---

### Task 1: Provider Response Normalizer

**Files:**
- Create: `area_assistant_agent/model_protocol.py`
- Create: `tests/test_model_protocol.py`

**Interfaces:**
- Consumes: decoded JSON-compatible provider response (`Any`).
- Produces: `normalize_chat_completion(payload: Any) -> Dict[str, Any]` with exact keys `content` and `tool_calls`.
- Produces: `ProtocolShapeError(Exception)` with `.shape: Dict[str, Any]` containing structural metadata only.
- Later tasks consume the normalized call shape `{"id": str, "name": str, "arguments": dict}`.

- [ ] **Step 1: Write failing normalization tests**

Cover standard content, array text content, standard calls, object arguments, legacy calls, and malformed payloads:

```python
class ModelProtocolTests(unittest.TestCase):
    def test_normalizes_standard_content(self):
        payload = {"choices": [{"message": {"content": "{\"ok\":true}"}}]}
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
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
& 'C:\Users\h2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest -v tests.test_model_protocol
```

Expected: import failure because `area_assistant_agent.model_protocol` does not exist.

- [ ] **Step 3: Implement the minimal normalizer**

Implement focused helpers in `model_protocol.py`:

```python
class ProtocolShapeError(Exception):
    def __init__(self, shape):
        super().__init__("provider response shape is incompatible")
        self.shape = shape


def normalize_chat_completion(payload):
    message = _first_message(payload)
    content = _normalize_content(message.get("content"))
    calls = _normalize_calls(message)
    if content is None and not calls:
        raise ProtocolShapeError(summarize_chat_completion_shape(payload))
    return {"content": content, "tool_calls": calls}
```

Use `json.loads` only for string arguments, reject non-object results, join only
`{"type":"text","text":str}` blocks, and synthesize only `legacy-call-0` for a
single legacy `function_call`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 command. Expected: all `tests.test_model_protocol` tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- area_assistant_agent/model_protocol.py tests/test_model_protocol.py
git commit -m "feat: normalize model provider responses"
```

### Task 2: Strict Tool and Final-Plan Schemas

**Files:**
- Modify: `area_assistant_agent/planning.py:76-104,286-345`
- Modify: `area_assistant_agent/model_api.py:106-160`
- Modify: `tests/test_planning_agent.py`
- Modify: `tests/test_model_planning_api.py`

**Interfaces:**
- Consumes: existing `TOOL_DEFINITIONS`, `PlanningResult`, and `planning_turn(messages, tools)` call path.
- Produces: `PLANNING_RESPONSE_FORMAT: Dict[str, Any]` using `type=json_schema`.
- Produces: `planning_turn(messages, tools, response_format=None) -> normalized turn`.
- Tool definitions keep existing names but add `function.strict = True` and closed parameter schemas.

- [ ] **Step 1: Write failing request-shape tests**

Extend the local HTTP handler test to capture and assert:

```python
self.assertEqual(server.received["response_format"]["type"], "json_schema")
schema = server.received["response_format"]["json_schema"]["schema"]
self.assertFalse(schema["additionalProperties"])
self.assertEqual(schema["properties"]["options"]["minItems"], 2)
self.assertEqual(schema["properties"]["options"]["maxItems"], 4)
self.assertTrue(server.received["tools"][0]["function"]["strict"])
self.assertFalse(
    server.received["tools"][0]["function"]["parameters"]["additionalProperties"]
)
```

Add a planning-agent test asserting it passes `PLANNING_RESPONSE_FORMAT` into
each model turn while exposing only `inspect_revit_model` and the conditionally
available `capture_revit_view` tool.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
& 'C:\Users\h2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest -v tests.test_model_planning_api tests.test_planning_agent
```

Expected: failure because `response_format` and strict tool metadata are absent.

- [ ] **Step 3: Define the exact final schema**

Add `PLANNING_RESPONSE_FORMAT` beside `TOOL_DEFINITIONS`. Require `summary`,
`question`, and `options`; option items require `id`, `label`, `recommended`,
`rationale`, and `impact`; set `additionalProperties: false` at every object
level and `minItems: 2`, `maxItems: 4` for options.

Add `strict: True` and `additionalProperties: False` to both read-only function
definitions. Keep current argument enums and required fields unchanged.

- [ ] **Step 4: Pass the response format through the client**

Change the signature and request body:

```python
def planning_turn(self, messages, tools, response_format=None):
    body_value = {
        "model": self._config.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": False,
    }
    if response_format is not None:
        body_value["response_format"] = response_format
```

Update scripted test doubles to accept `response_format=None` without changing
their returned turns. Call it from `PlanningAgent.plan()` with
`PLANNING_RESPONSE_FORMAT`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Task 2 command. Expected: all focused tests pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add -- area_assistant_agent/planning.py area_assistant_agent/model_api.py tests/test_planning_agent.py tests/test_model_planning_api.py
git commit -m "feat: constrain planning model output"
```

### Task 3: Value-Free Diagnostics and Additive Error Contract

**Files:**
- Modify: `area_assistant_agent/model_protocol.py`
- Create: `area_assistant_agent/model_diagnostics.py`
- Modify: `contracts/v1/actions/planning-job-status.schema.json`
- Modify: `contracts/v1/actions/examples/planning-job-status.json`
- Modify: `tests/test_model_protocol.py`
- Create: `tests/test_model_diagnostics.py`
- Modify: `tests/test_contract_examples.py`

**Interfaces:**
- Consumes: `ProtocolShapeError.shape` and canonical session directory.
- Produces: `summarize_chat_completion_shape(payload) -> Dict[str, Any]`.
- Produces: `persist_model_diagnostic(session_directory: Path, job_id: str, diagnostic_id: str, shape: dict) -> Path`.
- Produces: optional public error field `diagnostic_id` matching `^[A-Za-z0-9_-]{8,64}$`.

- [ ] **Step 1: Write failing sensitive-data tests**

Construct a payload containing sentinel secrets in content, reasoning, tool
name, arguments, Revit path, fingerprint, and authorization-like fields. Assert
the JSON serialization of the summary contains none of them but includes only
types, key names, and counts:

```python
serialized = json.dumps(summarize_chat_completion_shape(payload), sort_keys=True)
for forbidden in ("SECRET_KEY", "C:\\Secret.rvt", "inspect_secret", "token-value"):
    self.assertNotIn(forbidden, serialized)
self.assertEqual(summary["top_level"]["keys"], ["choices", "id"])
self.assertEqual(summary["choices"]["count"], 1)
```

Test atomic JSONL append to `session_directory / "model_diagnostics" /
"protocol.jsonl"` and assert the record contains `job_id`, `diagnostic_id`,
timestamp, and shape only.

- [ ] **Step 2: Write failing contract test**

Add `diagnostic_id` to a failed status example and validate it. Also assert an
invalid value containing whitespace is rejected by Draft 2020-12 validation.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
& 'C:\Users\h2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest -v tests.test_model_protocol tests.test_model_diagnostics tests.test_contract_examples
```

Expected: missing summarizer/persistence functions and schema rejection of the
new field.

- [ ] **Step 4: Implement shape-only summaries and atomic persistence**

Use allowlisted names only. For arbitrary provider keys, store the key name only
when it is one of `id`, `object`, `choices`, `message`, `content`, `tool_calls`,
`function_call`, `function`, `arguments`, `finish_reason`, `usage`, or
`reasoning_content`; otherwise record only `unknown_key_count`.

Write one JSON object per line using the existing persistence locking/atomic
patterns. Never pass the raw payload into `persist_model_diagnostic`.

- [ ] **Step 5: Update the v1 contract additively**

Add optional `diagnostic_id` only to failed/interrupted error objects, keep all
existing required fields, and update the failed example. Do not make the field
required for older clients or snapshots.

- [ ] **Step 6: Run focused tests and repository safety scan**

```powershell
& 'C:\Users\h2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest -v tests.test_model_protocol tests.test_model_diagnostics tests.test_contract_examples
& 'C:\Users\h2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/check_repository_safety.py
```

Expected: tests pass and safety scan prints `Repository safety check passed.`

- [ ] **Step 7: Commit Task 3**

```powershell
git add -- area_assistant_agent/model_protocol.py area_assistant_agent/model_diagnostics.py contracts/v1/actions/planning-job-status.schema.json contracts/v1/actions/examples/planning-job-status.json tests/test_model_protocol.py tests/test_model_diagnostics.py tests/test_contract_examples.py
git commit -m "feat: record safe model protocol diagnostics"
```

### Task 4: Transport and Durable-Job Integration

**Files:**
- Modify: `area_assistant_agent/model_api.py`
- Modify: `area_assistant_agent/server.py:232-282,595-720`
- Modify: `area_assistant_agent/planning_jobs.py`
- Modify: `area_assistant_pyrevit/client.py`
- Modify: `tests/test_model_planning_api.py`
- Modify: `tests/test_agent_chat_api.py`
- Modify: `tests/test_agent_planning_jobs_api.py`
- Modify: `tests/test_pyrevit_agent_client.py`

**Interfaces:**
- Consumes: `normalize_chat_completion`, `ProtocolShapeError`, and `persist_model_diagnostic`.
- Produces: `ModelApiError(code, message, retryable=True, diagnostic_id=None, diagnostic=None)`.
- Produces: contract-valid terminal job error with optional `diagnostic_id`.
- Keeps `planning_turn(messages, tools, response_format=None)` as the planning-agent boundary.

- [ ] **Step 1: Write failing transport classification tests**

Use local HTTP handlers and patched `urlopen` to assert:

```python
with self.assertRaises(ModelApiError) as caught:
    client.planning_turn(messages, tools, response_format)
self.assertEqual(caught.exception.code, "model_protocol_error")
self.assertEqual(str(caught.exception), "Model API returned an incompatible response.")
self.assertIsNotNone(caught.exception.diagnostic_id)
self.assertNotIn("secret", json.dumps(caught.exception.diagnostic))
```

Add distinct tests for HTTP status, socket timeout, and `URLError`, and assert
the normalizer is used for a valid content-block/tool-call fixture.

Add streaming-chat fixtures for a valid SSE response and a malformed SSE event.
The malformed event must raise the same safe `model_protocol_error`, attach a
shape summary containing event key names/types only, and never include delta
content in the public error or diagnostic record.

- [ ] **Step 2: Write failing durable-job diagnostic test**

Make the fake planning model raise a `ModelApiError` carrying a safe diagnostic.
Wait for terminal status and assert:

```python
self.assertEqual(terminal["state"], "failed")
self.assertEqual(terminal["error"]["code"], "model_protocol_error")
self.assertEqual(terminal["error"]["diagnostic_id"], "diag-test-1234")
self.assertEqual(self.model.call_count, 1)
self.assertEqual(len(list(session_directory.glob("model_diagnostics/*.jsonl"))), 1)
```

Poll the same job twice and assert neither poll invokes the model.

Add an Agent chat API test that sends one session message to a malformed local
SSE provider, receives one terminal safe error event, persists one diagnostic
under that session, and does not send a second provider request.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
& 'C:\Users\h2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest -v tests.test_model_planning_api tests.test_agent_chat_api tests.test_agent_planning_jobs_api tests.test_pyrevit_agent_client
```

Expected: missing diagnostic fields/persistence and old parser behavior.

- [ ] **Step 4: Integrate the normalizer and error object**

Decode the HTTP body once, call `normalize_chat_completion`, and translate only
`ProtocolShapeError` into:

```python
raise ModelApiError(
    "model_protocol_error",
    "Model API returned an incompatible response.",
    retryable=True,
    diagnostic_id=uuid.uuid4().hex,
    diagnostic=error.shape,
)
```

Keep raw response values out of exception messages and chained public output.
For `stream_reply`, summarize malformed SSE event structure with the same
allowlist and error object; valid delta behavior remains unchanged.

- [ ] **Step 5: Persist diagnostics at the job boundary**

In `_run_plan_job`, before publishing a `ModelApiError` terminal snapshot, call
`persist_model_diagnostic(registry.storage_root.parent, job_id, ...)` when a
diagnostic exists. Include only `diagnostic_id` in the public error. If the
diagnostic write fails, publish the original safe error without the ID and do
not replace it with a persistence exception.

In the chat handler's existing `ModelApiError` branch, resolve the already
validated canonical session directory and persist the same safe diagnostic
using the session/context identifier as the correlation ID. Emit the existing
single terminal error envelope and never reopen the provider connection.

Update planning-job validation and the IronPython client validator to accept
the optional identifier while rejecting unknown error fields.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the Task 4 command. Expected: all focused tests pass with one model call per
submitted job.

- [ ] **Step 7: Commit Task 4 selectively**

Before staging, inspect the existing launcher edits in
`area_assistant_pyrevit/client.py` and `tests/test_pyrevit_agent_client.py`.
Stage only Task 4 hunks with `git add -p`; never stage unrelated launcher hunks.

```powershell
git add -- area_assistant_agent/model_api.py area_assistant_agent/server.py area_assistant_agent/planning_jobs.py tests/test_model_planning_api.py tests/test_agent_chat_api.py tests/test_agent_planning_jobs_api.py
git add -p -- area_assistant_pyrevit/client.py tests/test_pyrevit_agent_client.py
git diff --cached --check
git commit -m "feat: integrate safe model protocol gateway"
```

### Task 5: Full Verification, Provider Gate, and Revit Handoff

**Files:**
- Modify: `README.md:40-55,120-140`
- Modify: `docs/issue-7-revit-manual-test.md`
- Modify: `.superpowers/sdd/2026-08-21-recoverable-planning-jobs/progress.md` (ignored local ledger only; do not commit)

**Interfaces:**
- Consumes: all Task 1-4 production interfaces and the confirmed user-level DeepSeek configuration.
- Produces: operator documentation, complete automated evidence, one provider-conformance result, and a manual Revit checklist. No merge or push.

- [ ] **Step 1: Document exact configuration and safety behavior**

Document `AI_AREA_ASSISTANT_BASE_URL=https://api.deepseek.com`,
`AI_AREA_ASSISTANT_MODEL=deepseek-v4-flash`, Agent restart requirements, strict
JSON behavior, diagnostic location/content exclusions, and the rule that
timeouts/protocol failures never auto-retry.

- [ ] **Step 2: Run the complete automated suite**

```powershell
$python='C:\Users\h2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $python -m unittest discover -s tests
& $python -m compileall -q area_assistant_agent area_assistant_pyrevit pyrevit scripts tests
& $python scripts/check_repository_safety.py
git diff --check
```

Expected: all tests pass, compile exits 0, safety scan passes, and diff check is
clean. Record the exact test count; never call a partial run fully green.

- [ ] **Step 3: Review the branch on Standards and Spec axes**

Use the `code-review` skill against fixed point `419aa10`. Resolve every Critical
or Important finding with TDD, rerun Task 5 Step 2 after each fix round, and do
not touch unrelated launcher changes.

- [ ] **Step 4: Request authorization for one provider conformance probe**

After local review is clean, stop and ask the user. On approval, send one small
non-Revit request using `deepseek-v4-flash`, strict tool definitions, and the
intended `response_format`. Verify one standard normalized tool call followed by
one schema-valid final JSON response. Persist only the allowed diagnostic shape
if it fails. Do not auto-retry.

- [ ] **Step 5: Request authorization for one Revit Scan and Plan**

Only after Step 4 passes, stop and ask separately. On approval, restart Revit and
Agent, verify the authorized detached copy and `IsModified: False`, create a new
session, select one valid source, and submit exactly one Scan and Plan. Observe
the durable job ID to one terminal state without clicking Retry or Scan again.

- [ ] **Step 6: Record manual evidence and no-write result**

Record timestamps, job ID, state/stage path, model-call count if available,
result-schema validation, `IsModified` before/after, and whether the RVT was
closed without saving. State any untested boundary explicitly.

- [ ] **Step 7: Commit documentation only**

```powershell
git add -- README.md docs/issue-7-revit-manual-test.md
git diff --cached --check
git commit -m "docs: document model gateway verification"
```

- [ ] **Step 8: Stop for integration decision**

Report commits, automated evidence, provider/Revit evidence, remaining manual
limits, and dirty unrelated files. Do not push, update PR #19, merge, or delete
worktrees without explicit user instruction.
