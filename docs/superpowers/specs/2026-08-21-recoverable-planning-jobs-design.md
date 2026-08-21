# Recoverable Planning Jobs Design

## Purpose

Replace the synchronous planning request with a recoverable background-job protocol. A pyRevit transport timeout must never be interpreted as proof that the planning work failed, and a retry must never create an accidental duplicate paid model request.

This is the second layer of the three-layer rewrite. It covers read-only analysis from the panel through the local Agent. It does not authorize or implement Revit model writes.

## Root Cause

The current panel calls `POST /v1/plans` and keeps one HTTP connection open while the Agent reads Revit, captures evidence, calls the model, validates the structured result, and persists it. The client applies a fixed timeout to that connection. When the connection times out, the server-side request thread can continue running, but the panel no longer has a durable identity with which to observe it. The panel therefore cannot distinguish a slow live task from a failed task and must block retries defensively.

## Chosen Architecture

Planning becomes a job resource with a stable `job_id`.

1. `POST /v1/plan-jobs` validates the current session and verified document binding, deduplicates the request, records the job, starts background execution, and immediately returns the job snapshot.
2. `GET /v1/plan-jobs/{job_id}` returns the latest validated snapshot without starting work.
3. The pyRevit panel polls the job approximately once per second on its existing background-thread mechanism and updates user-visible progress.
4. A polling transport failure means only that the latest status check failed. The panel keeps the same `job_id`, retries with a bounded delay, and does not submit another job.
5. Terminal results are accepted only when the panel session context and Revit document fingerprint still match the context captured at submission.

The legacy `POST /v1/plans` route remains temporarily available for compatibility tests and older callers. The rewritten panel uses only the job API.

## Job Identity and Deduplication

Each job has a random opaque `job_id`. The Agent also computes an idempotency key from these exact request fields:

- `panel_instance_id`
- `generation`
- `context_id`
- `document_fingerprint`
- `session_id`
- normalized planning message

While a matching job is `queued` or `running`, submitting the same request returns that job instead of starting another model call. A completed request is not silently replayed as a new paid call; the existing completed job is returned until the user takes a distinct action or changes the message.

## States and Progress

The public job states are:

- `queued`: accepted but not yet executing
- `running`: executing one of the read-only planning stages
- `completed`: contains a fully validated structured plan
- `failed`: contains a stable error code and safe message
- `cancelled`: revoked before a result could be committed
- `interrupted`: the Agent restarted while the persisted job was non-terminal

The public progress stages are:

- `accepted`
- `validating_context`
- `reading_model`
- `capturing_evidence`
- `requesting_model`
- `validating_result`
- `persisting_result`
- `finished`

Progress is best-effort and monotonic. Correctness depends on state and context fences, not on exact progress percentages.

## API Contracts

### Submit

`POST /v1/plan-jobs` uses the existing versioned request envelope with action `analysis.plan.submit`. Its payload contains the same session and document fields currently sent to `analysis.plan`, plus the planning message.

It returns HTTP 202 for a newly accepted job or HTTP 200 for a deduplicated existing job. The response payload contains:

- `job_id`
- `state`
- `stage`
- `created_at`
- `updated_at`
- `result` as `null` until completed
- `error` as `null` unless failed or interrupted

### Poll

`GET /v1/plan-jobs/{job_id}` returns the same job snapshot. Unknown IDs return a versioned `job_not_found` error. A job belonging to a different current panel/session context is not disclosed and returns `job_not_found`.

### Cancellation

`POST /v1/plan-jobs/{job_id}/cancel` marks a non-terminal job as cancelled and activates its context fence. Python threads are not forcibly terminated. Any later tool call, screenshot commit, conversation write, machine-state write, or result publication must fail the fence and remain invisible to the panel.

Cancellation is idempotent. Cancelling an already terminal job returns its existing terminal snapshot.

## Persistence and Restart Recovery

Job metadata is stored under the existing session directory using atomic replacement. Persist only contract-safe metadata, state, progress, timestamps, the validated final result, and sanitized errors. Do not persist credentials, request headers, raw model payloads, or arbitrary exception representations.

On Agent startup or first access, any persisted `queued` or `running` job is converted to `interrupted`. The Agent does not automatically replay a possibly paid model request. The user may explicitly submit again after seeing the interruption.

## Concurrency and Safety

- A dedicated job registry owns job lookup, persistence, state transitions, and deduplication.
- At most one planning job executes at a time, preserving the existing `planning_lock` behavior around rvt-mcp access.
- Every durable side effect is revalidated under `session_lock` immediately before commit.
- Session revocation and document switching cancel or invalidate matching non-terminal jobs.
- A stale job can never append an assistant message, update `last_plan`, commit a screenshot, or update the panel.
- Result validation remains identical to the current structured planning contract: two to four options and exactly one recommendation.

## Panel Behavior

- The first submit disables duplicate planning controls and stores `job_id` with the current session context.
- The panel shows a human-readable running state rather than `failed` when a status request times out.
- Polling uses short HTTP timeouts and bounded retry delay; it does not hold a planning-length HTTP connection.
- A completed result is rendered only if the captured session context is still current.
- `failed`, `cancelled`, and `interrupted` are distinct visible outcomes with an explicit retry action.
- Closing the pane does not start a duplicate request when it is reopened; the persisted job can be observed again from the same session.

## Error Semantics

- Transport timeout while submitting: query by the deterministic idempotency key before permitting another submit.
- Transport timeout while polling: retain `job_id`, show waiting/reconnecting state, and poll again.
- Agent-reported `failed`: show its sanitized error and enable explicit retry.
- Agent restart: show `interrupted`; never claim the old work is still running.
- Document/session mismatch: cancel local observation and reject all stale commits.

## Testing

Automated tests must prove:

1. Submission returns before a blocking planner completes.
2. Polling progresses from non-terminal to `completed` and returns a contract-valid plan.
3. Duplicate submissions cause exactly one planner/model invocation.
4. A polling timeout does not create a second job and later polling recovers.
5. Document switching or session revocation prevents every stale durable commit.
6. Persisted non-terminal jobs become `interrupted` after Agent recreation.
7. Unknown or cross-context job IDs are not disclosed.
8. Cancellation is idempotent and blocks late output.
9. The legacy synchronous route remains compatible while the panel stops using it.
10. Existing contract, session, document-binding, safety, and launcher tests remain green.

Manual Revit acceptance must prove that an analysis lasting longer than the former client timeout remains visibly running, produces one final result, and does not duplicate work after temporary polling failures or repeated clicks.

## Non-Goals

- No Revit model modification or transaction execution.
- No automatic replay after Agent restart.
- No multi-user or remote-network job service.
- No forced termination of Python worker threads.
- No percentage-complete estimate that cannot be supported by real stage transitions.

