# Shared communication contracts

`v1/` is the public boundary shared by the pyRevit client, local Agent and later rvt-mcp integration. It defines four message envelopes: request, response, state and error. Every message carries `contract_version: "1.0"` and a stable `message_type`.

Compatibility rules:

- Consumers must reject unsupported major versions rather than guessing.
- Backward-compatible optional fields may be added within v1.
- Required-field removal, field meaning changes, or enum narrowing require a new major directory such as `v2/`.
- Contract changes must update the schema, example and contract test in the same pull request.
- Feature-specific payload fields belong in later tickets; this baseline intentionally defines only the shared envelope.

The JSON Schema files are the source of truth. `examples/` contains reviewable sample messages, not real project data.

Feature contracts:

- [`v1/chat.md`](v1/chat.md) defines the backward-compatible `chat.stream` action and its streamed response payloads.
- `v1/actions/revit-document-status-request.schema.json` and `revit-document-status.schema.json` define the document-status request and binding result shared by the pyRevit panel, local Agent and rvt-mcp integration. They reuse the existing v1 request/response envelope fields without changing their meaning.
- `v1/actions/session-request.schema.json` and `session-response.schema.json` define the additive session open, explicit choice, message audit and document-switch revocation actions used by the pyRevit panel and local Agent.
- `v1/actions/planning-request.schema.json` and `planning-response.schema.json` define the additive `analysis.plan` action. It is bound to the active document session and returns two to four clickable options with exactly one recommendation, plus rationale and impact. The v1 envelope is unchanged.
