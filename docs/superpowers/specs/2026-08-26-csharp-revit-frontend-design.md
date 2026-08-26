# C# Revit Frontend Design

## Authoritative architecture decision

The project has abandoned pyRevit as an application platform. No subsequent
Issue may add to, repair, port, or depend on the pyRevit/IronPython pane,
launcher, startup script, or polling implementation. Those files are legacy
evidence only and are not part of the target product.

“Simple and effective” is the highest design rule for this project. Every
future change must implement the shortest reliable path required by the active
Issue. Do not add compatibility layers, replay systems, generalized framework
code, speculative recovery, or process ceremony unless a demonstrated failure
in the current Revit 2026 workflow requires it.

## Goal

Replace the pyRevit/IronPython pane with a small Revit 2026 C# add-in while
keeping the existing Python Agent and DeepSeek integration. The first release
must complete one reliable read-only path: select a boundary source, submit one
planning job, wait for that same job, and display the structured options.

## Supported environment

- Autodesk Revit 2026 only.
- C#/.NET add-in with a WPF dockable pane.
- Existing Python Agent on `127.0.0.1:8765`.
- Existing `deepseek-v4-flash` two-stage protocol:
  tool calls first, then `response_format={"type":"json_object"}`.

No cross-version Revit support is required.

## Components

### C# Revit add-in

The add-in owns the Revit-facing responsibilities only:

- register the Ribbon button and dockable pane;
- display Agent, document, selection, job, result, and error state;
- read the active document and selected Floor, Roof, or Wall through Revit API;
- create one planning request and poll the returned job ID;
- marshal any Revit API access through the supported Revit UI context;
- never call DeepSeek directly and never read or store the API key.

### Python Agent

The existing Agent remains responsible for:

- session and planning-job endpoints;
- Revit evidence requests already exposed by the Agent contract;
- DeepSeek tool calls and final JSON output;
- local validation of the final plan;
- safe diagnostics and the no-automatic-resubmit rule.

The existing pyRevit pane is abandoned and is not part of the new runtime path.

## Startup

When the user opens Area Assistant, the C# add-in calls `/health`. If the Agent
is unavailable, it starts the configured Python Agent process, waits for a
bounded readiness period, and calls `/health` again. A failure is shown with a
short message and the local log location. There is no infinite restart loop.

An already healthy Agent is reused. The Agent may remain running after Revit
closes.

## User flow

1. Open the Area Assistant pane from the Revit Ribbon.
2. The add-in verifies or starts the Python Agent.
3. The pane shows active document, view, and `IsModified`.
4. Select one or more Floor, Roof, or Wall elements in Revit.
5. Click **Read Current Selection**.
6. The add-in reads IDs, categories, types, levels, and boundary geometry.
7. Click **Scan and Plan** once.
8. The add-in submits one job and disables duplicate submission.
9. The add-in polls that same job ID once per second until terminal state.
10. On success, show the summary, question, and two to four options. On failure,
    show the returned safe error.

The first release is read-only and performs no Revit transaction.

## Session behavior

Opening the pane creates a new internal session for the active document without
asking the user to resume or create a session. Switching documents invalidates
the visible task and creates a new session for the new document.

A temporary status-query failure may retry the GET for the same job. It must
never resubmit the planning POST. There is no automatic model retry.

## Minimal UI

The pane contains only:

- Agent status;
- active document and view status;
- current boundary selection;
- **Read Current Selection**;
- **Scan and Plan**;
- structured plan result;
- plain error details.

## Explicitly out of scope

- Revit versions other than 2026;
- restoring, replaying, or choosing old sessions;
- general chat and a Send button;
- automatic retry of model requests;
- Area creation or any other Revit model write;
- migration of the existing pyRevit UI implementation;
- installers, auto-update, telemetry, and multi-user deployment.

## Acceptance criteria

1. Revit 2026 loads the add-in and opens its dockable pane without a pyRevit
   dependency.
2. Opening the pane reuses a healthy Agent or starts it and reports readiness.
3. A selected Wall can be read and submitted once for planning.
4. The pane observes the returned job through one terminal state and displays a
   valid two-to-four-option result or a clear terminal error.
5. Duplicate clicks never create a second planning job.
6. The active RVT reports `IsModified: False` before and after the full flow.

## Verification

- Unit-test Agent startup decisions, request construction, response parsing,
  and duplicate-submit blocking without Revit or DeepSeek.
- Build the add-in against the local Revit 2026 API assemblies.
- Perform one separately authorized manual run in the detached test RVT.
- Record the job ID, terminal state, and `IsModified` before and after.
