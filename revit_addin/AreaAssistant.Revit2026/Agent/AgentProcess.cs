using System.Diagnostics;

namespace AreaAssistant.Revit2026.Agent;

public sealed class AgentProcess
{
    private readonly AgentClient _client;
    private readonly string _pythonExecutable;
    private readonly string _workingDirectory;

    public AgentProcess(AgentClient client, string pythonExecutable, string workingDirectory)
    {
        _client = client;
        _pythonExecutable = pythonExecutable;
        _workingDirectory = workingDirectory;
    }

    public async Task<AgentReadiness> EnsureReadyAsync(CancellationToken cancellationToken)
    {
        if (await _client.IsHealthyAsync(cancellationToken)) return new(true, "Agent 已连接");

        var process = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = _pythonExecutable,
                Arguments = "-m area_assistant_agent --serve",
                WorkingDirectory = _workingDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            },
        };
        process.OutputDataReceived += (_, _) => { };
        process.ErrorDataReceived += (_, _) => { };
        if (!process.Start()) return new(false, "无法启动 Agent");
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (DateTime.UtcNow < deadline)
        {
            await Task.Delay(250, cancellationToken);
            if (await _client.IsHealthyAsync(cancellationToken)) return new(true, "Agent 已连接");
            if (process.HasExited) break;
        }
        return new(false, "Agent 启动超时");
    }
}
