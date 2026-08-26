# Model Protocol Gateway Design

Date: 2026-08-26

## Goal

Replace the Area Assistant's single-shape model response parser with a safe,
testable protocol gateway. The gateway must make the model boundary observable
without persisting prompts, Revit evidence, model output, or credentials, and
must produce one stable internal planning-turn shape for the existing planning
agent.

The production target for this phase is the DeepSeek OpenAI-compatible endpoint:

- base URL: `https://api.deepseek.com`
- model: `deepseek-v4-flash`
- API key: read only from `AI_AREA_ASSISTANT_API_KEY`

The experimental vision model is not the default. It timed out during the live
text-only selection request and is unnecessary while the connected Revit MCP
does not provide screenshot capture.

## Observed Failure

The former provider accepted the first planning turn but the current
`OpenAICompatibleClient.planning_turn()` rejected a later response as
`model_protocol_error`. The parser expected only a non-streaming Chat
Completions envelope at `choices[0].message`, with string `content` or standard
`tool_calls`.

A controlled DeepSeek check established that `deepseek-v4-flash` authenticates,
answers through `/chat/completions` in about two seconds, returns a standard
`chat.completion` envelope, and accepts `response_format: {"type":"json_object"}`.
The content assertion was inconclusive because the local diagnostic command
failed after the HTTP response arrived. This specification does not treat that
content as verified evidence.

## Design Principles

1. Fail closed on an unknown or malformed provider response.
2. Never automatically retry a model request after its billing outcome is
   uncertain.
3. Separate HTTP transport, provider-envelope normalization, and planning-result
   validation.
4. Constrain both tool arguments and the final planning result with schemas.
5. Record structure, not sensitive values, when protocol parsing fails.
6. Keep the planning agent independent of any provider-specific response shape.

## Components

### 1. Chat Completions transport

The transport owns URL construction, authorization headers, JSON encoding,
timeouts, HTTP status handling, and response-body decoding. It returns the
decoded JSON value to the normalizer and does not interpret assistant messages.

The transport continues to use `POST /chat/completions`. Planning requests are
non-streaming. Existing chat streaming remains a separate code path but shares
the same error taxonomy and safe diagnostics.

Transport errors remain distinct:

- `model_http_error`: the provider returned an HTTP failure status;
- `model_timeout`: an established request exceeded the configured timeout;
- `model_unavailable`: connection, DNS, or TLS transport failed;
- `model_protocol_error`: an HTTP success body could not be normalized.

No transport error triggers an automatic model resubmission.

### 2. Provider response normalizer

The normalizer accepts a decoded provider response and returns exactly:

```json
{
  "content": "string or null",
  "tool_calls": [
    {
      "id": "opaque call id",
      "name": "tool name",
      "arguments": {}
    }
  ]
}
```

It supports only explicitly tested Chat Completions variants:

- `choices[0].message.content` as a string or `null`;
- text content blocks represented as an array, normalized to one string;
- standard `message.tool_calls[].function`;
- function arguments encoded as a JSON string or already decoded object;
- legacy single `message.function_call`, when a deterministic synthetic call ID
  can be assigned locally.

Unknown content blocks, missing choices/messages, malformed tool calls,
non-object arguments, and a turn containing neither content nor calls fail
closed. Provider reasoning fields may be ignored only when a valid content or
tool-call result is also present; reasoning text is never treated as the final
planning result.

### 3. Strict tool definitions

Every model-visible function tool uses a JSON Schema with:

- `strict: true` where the provider supports it;
- `additionalProperties: false`;
- explicit required fields and enums;
- no model-write tools in the planning phase.

Local validation remains authoritative. Provider-side strictness improves
reliability but never replaces validation in `ReadOnlyRevitTools`.

### 4. Strict final planning output

The final non-tool planning turn requests Structured Outputs using
`response_format.type = json_schema`. Its schema matches the public planning
result contract:

- non-empty `summary`;
- non-empty `question`;
- two to four options;
- exactly the documented option fields;
- boolean `recommended`;
- no additional properties.

The existing `PlanningResult.from_dict()` validation remains the final local
gate, including the rule that exactly one option is recommended.

Because a planning turn may either call a tool or return the final answer, the
same JSON response format is sent on each turn only if DeepSeek accepts it
together with tools. A local conformance test and one separately authorized
minimal provider probe must establish that combination before it is enabled in
production. If the provider rejects the combination, the gateway uses strict
tool schemas during tool turns and applies the final response schema only on a
dedicated finalization turn.

### 5. Safe protocol diagnostics

When normalization fails, create a random `diagnostic_id` and append a JSONL
record under the current session's diagnostic directory. The record may contain
only:

- timestamp and diagnostic ID;
- endpoint family (`chat_completions` or `responses`);
- streaming flag;
- HTTP status and content type when available;
- top-level key names and value types;
- choice count and first-choice key names/types;
- message key names/types;
- content type and content-block type names;
- tool-call count and structural key names/types;
- exception category.

It must not contain:

- authorization headers or API keys;
- request messages, prompts, conversation history, or tool results;
- Revit element IDs, geometry, screenshots, document paths, or fingerprints;
- response content, reasoning content, tool names, tool argument values, or raw
  response bodies.

The public error remains safe and stable:

```json
{
  "code": "model_protocol_error",
  "message": "Model API returned an incompatible response.",
  "retryable": true,
  "diagnostic_id": "opaque id"
}
```

This phase adds optional `diagnostic_id` to the versioned v1 job-error schema
before the server publishes it. Existing consumers remain compatible because
the field is optional. The identifier carries no response content and only
correlates the safe public error with the local structural diagnostic.

## Data Flow

1. The panel submits one durable planning job.
2. The planning agent builds messages and strict read-only tool definitions.
3. The transport sends one non-streaming Chat Completions request.
4. The normalizer converts the provider envelope into the internal turn shape.
5. A tool call is locally validated, executed read-only, audited, and returned
   to the next turn; or final content is validated against the planning result
   contract.
6. The durable job publishes `completed` or one explicit terminal error.
7. A timeout or protocol error never causes the gateway or panel to submit a
   replacement model request automatically.

## Configuration and Startup

The Agent reads model configuration once at process start. Changing a user
environment variable does not mutate a running Agent. Revit/Agent must be fully
restarted before live verification, and health checks must precede any model
request.

The API key is never committed, printed, or copied into a diagnostic fixture.
Because a key was pasted into chat during manual setup, it should be rotated in
the DeepSeek console after testing and replaced locally in the Windows user
environment.

## Testing

### Unit tests

Use local HTTP fixtures for:

- standard string content;
- valid JSON planning content;
- array text content;
- standard and legacy tool calls;
- string and object tool arguments;
- reasoning-only responses;
- missing/malformed choices and messages;
- invalid JSON arguments and unknown content blocks;
- safe diagnostic summaries with forbidden-value scans;
- HTTP error, timeout, and unavailable transport classification;
- proof that no error path resubmits a model request.

### Contract tests

- Validate the strict final response schema against valid and invalid examples.
- Validate strict tool parameter schemas.
- Add optional `diagnostic_id` to the error schema and update examples
  additively before server/client changes consume it.

### Provider conformance probe

After all local tests pass, ask for explicit authorization for one minimal
DeepSeek request that combines tools with the intended response format. Record
only the safe shape summary and pass/fail result. Do not use Revit evidence for
this probe.

### Manual Revit acceptance

After the provider probe passes:

1. restart Revit and the Agent;
2. verify the authorized development copy and `IsModified: False`;
3. create a new session and select one valid boundary source;
4. submit exactly one Scan and Plan;
5. observe one durable job through terminal completion;
6. confirm a schema-valid plan appears and no duplicate request was made;
7. confirm `IsModified: False` and close without saving.

Every potentially billable retry requires separate user authorization.

## Out of Scope

- Revit model writes or third-layer Area creation;
- automatic provider fallback;
- automatic retry or hedged requests;
- storing raw provider responses;
- migrating the production path to the Responses API;
- enabling the experimental vision model while screenshot evidence is
  unavailable.

## Acceptance Criteria

- The planning agent consumes one provider-independent turn shape.
- Known response variants are fixture-tested; unknown variants fail closed.
- Final planning content is constrained by JSON Schema and locally validated.
- Tool arguments are schema-constrained and locally validated.
- Protocol diagnostics contain structural metadata only and pass sensitive-data
  scans.
- Error messages accurately distinguish timeout, transport, HTTP, and protocol
  failures.
- No failure path automatically creates another model request.
- One authorized DeepSeek conformance probe and one authorized Revit planning
  run complete before the third layer is called production-ready.
