# C# Revit Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build a Revit 2026 C# dockable pane that starts or reuses the existing Python Agent, reads selected boundary elements, submits exactly one planning job, and displays its terminal result without modifying Revit.

**Architecture:** A small .NET 8 WPF add-in owns Revit UI and Revit API access. It calls the existing localhost Agent through HttpClient; Python remains the only DeepSeek client. A fresh internal session is created per active document and only the returned job ID is polled.

**Tech Stack:** Revit 2026 API, C# 12, .NET 8 Windows/WPF, HttpClient, System.Text.Json, xUnit.

**Spec:** docs/superpowers/specs/2026-08-26-csharp-revit-frontend-design.md

## Global Constraints

- Autodesk Revit 2026 only.
- Reference C:\Program Files\Autodesk\Revit 2026\RevitAPI.dll and RevitAPIUI.dll with Private=false.
- C# never reads the DeepSeek key or calls DeepSeek.
- Read-only: never open a Revit Transaction.
- One click creates at most one planning POST; recovery may repeat GET for the same job only.
- Do not migrate the pyRevit UI or add session replay, chat, Send, automatic model retry, installers, telemetry, or multi-version support.
- Preserve unrelated dirty pyRevit launcher changes.

---

### Task 1: Revit 2026 Add-in Shell

**Files:**
- Create: revit_addin/AreaAssistant.Revit2026/AreaAssistant.Revit2026.csproj
- Create: revit_addin/AreaAssistant.Revit2026/App.cs
- Create: revit_addin/AreaAssistant.Revit2026/ShowPaneCommand.cs
- Create: revit_addin/AreaAssistant.Revit2026/AreaAssistantPaneProvider.cs
- Create: revit_addin/AreaAssistant.Revit2026/AreaAssistantPane.xaml
- Create: revit_addin/AreaAssistant.Revit2026/AreaAssistantPane.xaml.cs
- Create: revit_addin/AreaAssistant.Revit2026/AreaAssistant.Revit2026.addin

**Interfaces:**
- Produces App : IExternalApplication registering one Ribbon button and one DockablePaneId.
- Produces ShowPaneCommand : IExternalCommand showing the pane.
- Produces AreaAssistantPane : Page with status, selection, scan, result, and error controls.

- [ ] Step 1: Create a net8.0-windows, x64, UseWPF project with direct Revit 2026 references; run dotnet build and verify it fails before source exists.
- [ ] Step 2: Implement App.OnStartup with fixed pane GUID 42FC2674-60BC-4F90-ACAB-4BB20FC85F18, register the WPF page, and create one Ribbon button.
- [ ] Step 3: Implement ShowPaneCommand to call UIApplication.GetDockablePane(id).Show().
- [ ] Step 4: Create the minimal XAML: Agent status, document status, selection text, Read Current Selection, Scan and Plan, result list, and error text.
- [ ] Step 5: Add a fixed .addin manifest for Application and Command; do not register pyRevit.
- [ ] Step 6: Run dotnet build revit_addin/AreaAssistant.Revit2026/AreaAssistant.Revit2026.csproj -c Debug and verify the DLL and WPF resources exist.
- [ ] Step 7: Commit only revit_addin/AreaAssistant.Revit2026 with message feat: add Revit 2026 C# pane shell.

### Task 2: Agent Startup and One-job HTTP Client

**Files:**
- Create: revit_addin/AreaAssistant.Revit2026/Agent/AgentProcess.cs
- Create: revit_addin/AreaAssistant.Revit2026/Agent/AgentClient.cs
- Create: revit_addin/AreaAssistant.Revit2026/Agent/Contracts.cs
- Create: revit_addin/AreaAssistant.Revit2026/Planning/PlanningCoordinator.cs
- Create: revit_addin/AreaAssistant.Revit2026.Tests/AreaAssistant.Revit2026.Tests.csproj
- Create: revit_addin/AreaAssistant.Revit2026.Tests/AgentClientTests.cs
- Create: revit_addin/AreaAssistant.Revit2026.Tests/PlanningCoordinatorTests.cs

**Interfaces:**
- AgentProcess.EnsureReadyAsync(CancellationToken) returns AgentReadiness.
- AgentClient exposes OpenNewSessionAsync, SubmitPlanAsync, and GetPlanJobAsync.
- PlanningCoordinator exposes SubmitOnceAsync and RefreshAsync.
- Typed records represent session identity, selected sources, job snapshot, plan, option, and safe error.

- [ ] Step 1: Write a fake HttpMessageHandler test: calling SubmitOnceAsync twice produces exactly one POST to /v1/plan-jobs.
- [ ] Step 2: Add a test that RefreshAsync repeats only GET for the stored job ID and a transport failure never issues POST.
- [ ] Step 3: Add a completed fixture with three options and verify exactly one is recommended.
- [ ] Step 4: Run dotnet test and verify RED because the Agent classes do not exist.
- [ ] Step 5: Implement EnsureReadyAsync: call /health once, start the configured Python command only if unavailable, then health-check for no more than ten seconds. Use CreateNoWindow=true and redirected local logs; never inspect the API-key environment variable.
- [ ] Step 6: Implement exact v1 JSON DTOs with System.Text.Json. Reject missing fields, option counts outside 2-4, or recommended count other than one.
- [ ] Step 7: Implement SubmitOnceAsync so an existing job ID causes RefreshAsync, never a second submit. Implement RefreshAsync as GET of that job ID only.
- [ ] Step 8: Run dotnet test and commit the two projects with message feat: connect C# pane to local Agent.

### Task 3: Read-only Revit Selection Bridge

**Files:**
- Create: revit_addin/AreaAssistant.Revit2026/Revit/RevitContext.cs
- Create: revit_addin/AreaAssistant.Revit2026/Revit/ReadSelectionHandler.cs
- Create: revit_addin/AreaAssistant.Revit2026/Revit/SelectedSource.cs
- Modify: revit_addin/AreaAssistant.Revit2026/AreaAssistantPane.xaml.cs
- Create: revit_addin/AreaAssistant.Revit2026.Tests/SelectedSourceTests.cs

**Interfaces:**
- RevitContext tracks the current UIApplication and document identity.
- ReadSelectionHandler : IExternalEventHandler returns immutable SelectedSource values.
- Only Floor, RoofBase, and Wall are accepted.

- [ ] Step 1: Write a failing SelectedSource serialization test covering ID, UniqueId, category, type, level, bounds, and geometry.
- [ ] Step 2: Run dotnet test and verify RED because SelectedSource is absent.
- [ ] Step 3: Implement one ExternalEvent. In Execute, read UIDocument.Selection.GetElementIds(), filter Floor/RoofBase/Wall, and map values into immutable DTOs.
- [ ] Step 4: Include location/profile geometry needed by the current Agent request. Never start a Transaction and never retain live Element objects after Execute returns.
- [ ] Step 5: Before selection and scan, compare the active document identity. On change, stop visible polling, discard the job ID, and create a fresh internal Agent session; never restore the old session.
- [ ] Step 6: Run dotnet test and dotnet build, then commit with message feat: read Revit boundary selections in C#.

### Task 4: Pane Flow, Installation, and Manual Gate

**Files:**
- Create: revit_addin/AreaAssistant.Revit2026/Planning/PaneState.cs
- Modify: revit_addin/AreaAssistant.Revit2026/AreaAssistantPane.xaml
- Modify: revit_addin/AreaAssistant.Revit2026/AreaAssistantPane.xaml.cs
- Modify: README.md
- Create: docs/csharp-revit-2026-manual-test.md

**Interfaces:**
- Visible states are StartingAgent, Ready, SelectionReady, Running, Completed, and Failed.
- Consumes Agent readiness/client, internal session identity, SelectedSource, and job DTOs.

- [ ] Step 1: Write failing state tests: Scan is enabled only in SelectionReady, disabled in Running, and duplicate clicks cannot submit.
- [ ] Step 2: On pane load, ensure Agent readiness and create a new internal session.
- [ ] Step 3: On Scan, call SubmitOnceAsync once, disable Scan, and start a one-second WPF DispatcherTimer that calls RefreshAsync for the same job. Stop at terminal state. Never block the Revit UI thread.
- [ ] Step 4: Render summary, question, and option cards; render safe terminal code/message. Do not add Retry or Send.
- [ ] Step 5: Run dotnet test, Release build, the complete Python unittest discovery, repository safety, and git diff --check. Record exact results.
- [ ] Step 6: Copy Release output and the manifest to %APPDATA%\Autodesk\Revit\Addins\2026\AreaAssistant. Disable the pyRevit extension for the manual run so only one UI registers.
- [ ] Step 7: Stop and request authorization. Then run exactly one test in the detached RVT: record IsModified before, select one Wall, submit once, observe the same job to terminal state, record IsModified after, and do not save.
- [ ] Step 8: Commit README, manual evidence, and add-in changes with message feat: complete Revit 2026 C# planning pane. Do not push or merge.

