# Streaming chat contract

The pyRevit panel sends a `request` envelope with `action: "chat.stream"`. Its payload contains one non-empty `message` string. The local Agent answers as newline-delimited JSON using the existing v1 envelopes:

1. An `accepted` response with `payload.event: "started"`.
2. Zero or more `accepted` responses whose payload contains a non-empty `delta` string.
3. One `completed` response whose payload contains the complete `message` string, or one `error` envelope using the shared error contract.

Every streamed envelope repeats the request's `request_id`. This is an additive v1 feature: it does not add required envelope fields or change the meaning of existing fields. Consumers that do not implement `chat.stream` may reject or ignore that action.

`chat-request.schema.json` and `chat-response.schema.json` compose the shared envelope schemas with these feature payload constraints.
