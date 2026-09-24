using System.Net;
using System.Text;
using System.Text.Json;
using AreaAssistant.Revit2026.Agent;
using AreaAssistant.Revit2026.Planning;
using Xunit;

namespace AreaAssistant.Revit2026.Tests;

public sealed class PlanningCoordinatorTests
{
    private static readonly SessionIdentity Identity = new("panel", 1, "context", "document", "session", "D:\\project");

    [Fact]
    public async Task SubmitTwicePostsOnceAndThenGetsSameJob()
    {
        var handler = new FakeHandler(
            Job("accepted", "queued"),
            Job("accepted", "running"));
        var coordinator = Coordinator(handler);

        await coordinator.SubmitOnceAsync(Identity, "plan", default);
        await coordinator.SubmitOnceAsync(Identity, "plan", default);

        Assert.Single(handler.Requests, request => request.Method == HttpMethod.Post);
        Assert.Contains(handler.Requests, request => request.Method == HttpMethod.Get && request.Uri.Contains("/v1/plan-jobs/job-1?"));
    }

    [Fact]
    public async Task RefreshTransportFailureDoesNotPostAgain()
    {
        var handler = new FakeHandler(Job("accepted", "queued"), new HttpRequestException("offline"));
        var coordinator = Coordinator(handler);
        await coordinator.SubmitOnceAsync(Identity, "plan", default);

        await Assert.ThrowsAsync<HttpRequestException>(() => coordinator.RefreshAsync(Identity, default));

        Assert.Single(handler.Requests, request => request.Method == HttpMethod.Post);
    }

    [Fact]
    public async Task CompletedPlanRequiresExactlyOneRecommendation()
    {
        var options = """
            [{"id":"a","label":"A","recommended":true,"rationale":"r","impact":"i"},
             {"id":"b","label":"B","recommended":false,"rationale":"r","impact":"i"},
             {"id":"c","label":"C","recommended":false,"rationale":"r","impact":"i"}]
            """;
        var handler = new FakeHandler(Job("completed", "completed", options));

        var result = await Coordinator(handler).SubmitOnceAsync(Identity, "plan", default);

        Assert.Equal(3, result.Result!.Options.Count);
        Assert.Single(result.Result.Options, option => option.Recommended);
    }

    [Fact]
    public async Task InvalidRecommendationCountIsRejected()
    {
        var options = """
            [{"id":"a","label":"A","recommended":true,"rationale":"r","impact":"i"},
             {"id":"b","label":"B","recommended":true,"rationale":"r","impact":"i"}]
            """;
        var coordinator = Coordinator(new FakeHandler(Job("completed", "completed", options)));

        await Assert.ThrowsAsync<InvalidDataException>(() => coordinator.SubmitOnceAsync(Identity, "plan", default));
    }

    [Fact]
    public async Task AgentErrorBodyIsShownInsteadOfGenericHttpStatus()
    {
        var handler = new FakeHandler(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
        {
            Content = new StringContent("{\"code\":\"document_status_unavailable\",\"message\":\"rvt-mcp is not ready\"}", Encoding.UTF8, "application/json"),
        });

        var error = await Assert.ThrowsAsync<InvalidOperationException>(
            () => Coordinator(handler).SubmitOnceAsync(Identity, "plan", default));

        Assert.Contains("rvt-mcp is not ready", error.Message);
    }

    [Fact]
    public async Task PostsUseAnExplicitContentLength()
    {
        var handler = new FakeHandler(Job("accepted", "queued"));

        await Coordinator(handler).SubmitOnceAsync(Identity, "plan", default);

        var request = Assert.Single(handler.Requests, request => request.Method == HttpMethod.Post);
        Assert.True(request.ContentLength > 0);
        Assert.NotEqual(true, request.Chunked);
    }

    private static PlanningCoordinator Coordinator(FakeHandler handler) =>
        new(new AgentClient(new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8765/") }));

    private static string Job(string status, string state, string? options = null)
    {
        object? result = options is null
            ? null
            : new { summary = "s", question = "q", options = JsonSerializer.Deserialize<JsonElement>(options) };
        return JsonSerializer.Serialize(new
        {
            contract_version = "1.0",
            message_type = "response",
            request_id = "r",
            status,
            payload = new { job_id = "job-1", state, stage = "finished", result, error = (object?)null },
        });
    }

    private sealed class FakeHandler(params object[] responses) : HttpMessageHandler
    {
        private readonly Queue<object> _responses = new(responses);
        public List<(HttpMethod Method, string Uri, long? ContentLength, bool? Chunked)> Requests { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Requests.Add((
                request.Method,
                request.RequestUri!.ToString(),
                request.Content?.Headers.ContentLength,
                request.Headers.TransferEncodingChunked));
            var next = _responses.Dequeue();
            if (next is Exception error) return Task.FromException<HttpResponseMessage>(error);
            if (next is HttpResponseMessage response) return Task.FromResult(response);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent((string)next, Encoding.UTF8, "application/json"),
            });
        }
    }
}
