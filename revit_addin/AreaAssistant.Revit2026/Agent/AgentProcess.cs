using System.Diagnostics;
using System.IO;

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

        var logDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AI Area Assistant");
        Directory.CreateDirectory(logDirectory);
        var logPath = Path.Combine(logDirectory, "agent.log");
        var logLock = new object();
        void WriteLog(string? line)
        {
            if (line is null) return;
            lock (logLock) File.AppendAllText(logPath, line + Environment.NewLine);
        }

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
        process.OutputDataReceived += (_, args) => WriteLog(args.Data);
        process.ErrorDataReceived += (_, args) => WriteLog(args.Data);
        if (!process.Start()) return new(false, $"无法启动 Agent。日志：{logPath}");
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (DateTime.UtcNow < deadline)
        {
            await Task.Delay(250, cancellationToken);
            if (await _client.IsHealthyAsync(cancellationToken)) return new(true, "Agent 已连接");
            if (process.HasExited) break;
        }
        return new(false, $"Agent 启动失败或超时。日志：{logPath}");
    }
}
