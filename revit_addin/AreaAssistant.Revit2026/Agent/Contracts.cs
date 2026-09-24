using System.Text.Json.Serialization;

namespace AreaAssistant.Revit2026.Agent;

public sealed record AgentReadiness(bool IsReady, string Message);

public sealed record ActiveViewSnapshot(string Id, string Name);
public sealed record DocumentSnapshot(
    [property: JsonPropertyName("revit_instance_id")] string RevitInstanceId,
    [property: JsonPropertyName("document_title")] string DocumentTitle,
    [property: JsonPropertyName("document_path")] string DocumentPath,
    [property: JsonPropertyName("document_fingerprint")] string DocumentFingerprint,
    [property: JsonPropertyName("active_view")] ActiveViewSnapshot ActiveView,
    [property: JsonPropertyName("is_modified")] bool IsModified);
public sealed record DocumentBinding(
    [property: JsonPropertyName("binding_status")] string BindingStatus,
    [property: JsonPropertyName("rvt_mcp_status")] string RvtMcpStatus,
    [property: JsonPropertyName("write_allowed")] bool WriteAllowed,
    [property: JsonPropertyName("pause_reason")] string? PauseReason);

public sealed record SessionIdentity(
    [property: JsonPropertyName("panel_instance_id")] string PanelInstanceId,
    [property: JsonPropertyName("generation")] int Generation,
    [property: JsonPropertyName("context_id")] string ContextId,
    [property: JsonPropertyName("document_fingerprint")] string DocumentFingerprint,
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("project_directory")] string ProjectDirectory);

public sealed record PlanOption(string Id, string Label, bool Recommended, string Rationale, string Impact);
public sealed record PlanResult(string Summary, string Question, IReadOnlyList<PlanOption> Options);
public sealed record SafeError(string Code, string Message, bool Retryable);
public sealed record PlanJobSnapshot(
    [property: JsonPropertyName("job_id")] string JobId,
    string State,
    string Stage,
    PlanResult? Result,
    SafeError? Error);

internal sealed record RequestEnvelope<T>(
    [property: JsonPropertyName("contract_version")] string ContractVersion,
    [property: JsonPropertyName("message_type")] string MessageType,
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("action")] string Action,
    [property: JsonPropertyName("payload")] T Payload);

internal sealed record ResponseEnvelope<T>([property: JsonPropertyName("payload")] T Payload);
