using System.IO;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;

namespace AreaAssistant.Revit2026.Agent;

public sealed class AgentClient
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly HttpClient _http;

    public AgentClient(HttpClient http) => _http = http;

    public async Task<bool> IsHealthyAsync(CancellationToken cancellationToken)
    {
        using var healthTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        healthTimeout.CancelAfter(TimeSpan.FromSeconds(1));
        try
        {
            using var response = await _http.GetAsync("health", healthTimeout.Token);
            if (!response.IsSuccessStatusCode) return false;
            var envelope = await response.Content.ReadFromJsonAsync<ResponseEnvelope<JsonElement>>(JsonOptions, healthTimeout.Token);
            return envelope is not null
                && envelope.Payload.TryGetProperty("service", out var service)
                && service.GetString() == "revit-ai-area-assistant-agent";
        }
        catch (HttpRequestException)
        {
            return false;
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return false;
        }
    }

    public async Task<SessionIdentity> OpenNewSessionAsync(
        string panelInstanceId, int generation, string documentFingerprint,
        string projectDirectory, CancellationToken cancellationToken)
    {
        var common = new Dictionary<string, object?>
        {
            ["panel_instance_id"] = panelInstanceId,
            ["generation"] = generation,
            ["document_fingerprint"] = documentFingerprint,
            ["project_directory"] = projectDirectory,
        };
        var opened = await PostAsync<Dictionary<string, object?>, JsonElement>(
            "v1/sessions/open", "session.open", common, cancellationToken);
        var contextId = RequiredString(opened, "context_id");
        common["context_id"] = contextId;
        common["choice"] = "new";
        common["session_id"] = null;
        var chosen = await PostAsync<Dictionary<string, object?>, JsonElement>(
            "v1/sessions/choose", "session.choose", common, cancellationToken);
        return new SessionIdentity(panelInstanceId, generation, contextId, documentFingerprint,
            RequiredString(chosen, "active_session_id"), projectDirectory);
    }

    public Task<DocumentBinding> BindDocumentAsync(DocumentSnapshot snapshot, CancellationToken cancellationToken) =>
        PostAsync<Dictionary<string, object?>, DocumentBinding>(
            "v1/document-status",
            "revit.document_status",
            new Dictionary<string, object?>
            {
                ["current_document"] = snapshot,
                ["previous_document"] = null,
                ["previous_pause_reason"] = null,
                ["allow_document_rebind"] = true,
            },
            cancellationToken);

    public Task<PlanJobSnapshot> SubmitPlanAsync(
        SessionIdentity identity, string message, CancellationToken cancellationToken)
    {
        var payload = IdentityPayload(identity);
        payload["message"] = message;
        payload["retry_terminal"] = false;
        return PostAsync<Dictionary<string, object?>, PlanJobSnapshot>(
            "v1/plan-jobs", "analysis.plan.submit", payload, cancellationToken);
    }

    public async Task<PlanJobSnapshot> GetPlanJobAsync(
        SessionIdentity identity, string jobId, CancellationToken cancellationToken)
    {
        var query = string.Join("&", IdentityPayload(identity)
            .Select(item => $"{Uri.EscapeDataString(item.Key)}={Uri.EscapeDataString(Convert.ToString(item.Value, System.Globalization.CultureInfo.InvariantCulture) ?? "")}"));
        using var response = await _http.GetAsync($"v1/plan-jobs/{Uri.EscapeDataString(jobId)}?{query}", cancellationToken);
        response.EnsureSuccessStatusCode();
        var envelope = await response.Content.ReadFromJsonAsync<ResponseEnvelope<PlanJobSnapshot>>(JsonOptions, cancellationToken)
            ?? throw new InvalidDataException("Agent returned an empty response.");
        Validate(envelope.Payload);
        return envelope.Payload;
    }

    private async Task<TResponse> PostAsync<TPayload, TResponse>(
        string path, string action, TPayload payload, CancellationToken cancellationToken)
    {
        var request = new RequestEnvelope<TPayload>("1.0", "request", Guid.NewGuid().ToString("N"), action, payload);
        using var response = await _http.PostAsJsonAsync(path, request, JsonOptions, cancellationToken);
        response.EnsureSuccessStatusCode();
        var envelope = await response.Content.ReadFromJsonAsync<ResponseEnvelope<TResponse>>(JsonOptions, cancellationToken)
            ?? throw new InvalidDataException("Agent returned an empty response.");
        if (envelope.Payload is PlanJobSnapshot snapshot) Validate(snapshot);
        return envelope.Payload;
    }

    private static Dictionary<string, object?> IdentityPayload(SessionIdentity identity) => new()
    {
        ["panel_instance_id"] = identity.PanelInstanceId,
        ["generation"] = identity.Generation,
        ["context_id"] = identity.ContextId,
        ["document_fingerprint"] = identity.DocumentFingerprint,
        ["session_id"] = identity.SessionId,
        ["project_directory"] = identity.ProjectDirectory,
    };

    private static string RequiredString(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(value.GetString())
            ? value.GetString()!
            : throw new InvalidDataException($"Agent response is missing {name}.");

    private static void Validate(PlanJobSnapshot snapshot)
    {
        if (string.IsNullOrWhiteSpace(snapshot.JobId) || string.IsNullOrWhiteSpace(snapshot.State))
            throw new InvalidDataException("Agent returned an invalid job.");
        if (string.IsNullOrWhiteSpace(snapshot.Stage))
            throw new InvalidDataException("Agent returned an invalid job stage.");
        if (snapshot.Result is null) return;
        if (string.IsNullOrWhiteSpace(snapshot.Result.Summary) || string.IsNullOrWhiteSpace(snapshot.Result.Question))
            throw new InvalidDataException("Agent returned an incomplete planning result.");
        if (snapshot.Result.Options.Count is < 2 or > 4 || snapshot.Result.Options.Count(option => option.Recommended) != 1)
            throw new InvalidDataException("Agent returned invalid planning options.");
        if (snapshot.Result.Options.Any(option =>
            string.IsNullOrWhiteSpace(option.Id)
            || string.IsNullOrWhiteSpace(option.Label)
            || string.IsNullOrWhiteSpace(option.Rationale)
            || string.IsNullOrWhiteSpace(option.Impact)))
            throw new InvalidDataException("Agent returned an incomplete planning option.");
    }
}
