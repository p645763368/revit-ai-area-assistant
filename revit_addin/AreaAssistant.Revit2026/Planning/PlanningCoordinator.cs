using AreaAssistant.Revit2026.Agent;

namespace AreaAssistant.Revit2026.Planning;

public sealed class PlanningCoordinator
{
    private readonly AgentClient _client;
    private string? _jobId;

    public PlanningCoordinator(AgentClient client) => _client = client;
    public string? JobId => _jobId;

    public async Task<PlanJobSnapshot> SubmitOnceAsync(
        SessionIdentity identity, string message, CancellationToken cancellationToken)
    {
        if (_jobId is not null) return await RefreshAsync(identity, cancellationToken);
        var snapshot = await _client.SubmitPlanAsync(identity, message, cancellationToken);
        _jobId = snapshot.JobId;
        return snapshot;
    }

    public Task<PlanJobSnapshot> RefreshAsync(SessionIdentity identity, CancellationToken cancellationToken) =>
        _jobId is null
            ? throw new InvalidOperationException("No planning job has been submitted.")
            : _client.GetPlanJobAsync(identity, _jobId, cancellationToken);
}
