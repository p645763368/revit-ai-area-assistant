using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Windows.Controls;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Events;
using AreaAssistant.Revit2026.Agent;
using AreaAssistant.Revit2026.Planning;
using AreaAssistant.Revit2026.Revit;

namespace AreaAssistant.Revit2026;

public partial class AreaAssistantPane : Page
{
    private readonly ReadSelectionHandler _selectionHandler;
    private readonly ExternalEvent _selectionEvent;
    private readonly AgentClient _agentClient;
    private RevitContext? _context;
    private RevitDocumentData? _documentData;
    private SessionIdentity? _session;
    private PlanningCoordinator? _planning;
    private IReadOnlyList<SelectedSource> _sources = [];
    private PaneState _state = new(PanePhase.StartingAgent);
    private int _generation;
    private bool _scanAfterSelection;
    private CancellationTokenSource? _pollCancellation;
    private bool _initializing;
    private int _initializationAttempts;
    private DateTime _nextInitializationAttemptUtc;

    public AreaAssistantPane()
    {
        InitializeComponent();
        _selectionHandler = new ReadSelectionHandler(ShowSelection);
        _selectionEvent = ExternalEvent.Create(_selectionHandler);
        _agentClient = new AgentClient(new HttpClient
        {
            BaseAddress = new Uri("http://127.0.0.1:8765/"),
            Timeout = TimeSpan.FromSeconds(45),
        });
    }

    internal async void Attach(UIApplication application)
    {
        var uiDocument = application.ActiveUIDocument;
        if (uiDocument is null)
        {
            _context = null;
            AgentStatusText.Text = "等待文档";
            DocumentStatusText.Text = "请打开 Revit 文档";
            SetState(PanePhase.StartingAgent);
            return;
        }
        try
        {
            _context = new RevitContext(application);
            _documentData = RevitContext.Capture(uiDocument.Document);
            ShowDocument();
            await EnsureDocumentSessionAsync(_documentData);
        }
        catch (Exception error)
        {
            ErrorText.Text = error.Message;
            SetState(PanePhase.Failed);
        }
    }

    internal void OnIdling(object? sender, IdlingEventArgs e)
    {
        if (sender is not UIApplication application) return;
        if (application.ActiveUIDocument is null)
        {
            if (_context is not null) _pollCancellation?.Cancel();
            _context = null;
            _session = null;
            _planning = null;
            AgentStatusText.Text = "等待文档";
            DocumentStatusText.Text = "请打开 Revit 文档";
            SetState(PanePhase.StartingAgent);
            return;
        }
        if (_context is null)
        {
            Attach(application);
            return;
        }
        if (_session is null)
        {
            if (!_initializing
                && _documentData is not null
                && _initializationAttempts < 3
                && DateTime.UtcNow >= _nextInitializationAttemptUtc)
                _ = EnsureDocumentSessionAsync(_documentData);
            return;
        }
        var currentIdentity = RevitContext.CreateIdentity(_context.CurrentDocument);
        if (currentIdentity == _session.DocumentFingerprint) return;
        _pollCancellation?.Cancel();
        _session = null;
        _planning = null;
        _sources = [];
        SelectionText.Text = "文档已变化，请重新读取当前选择";
        ResultText.Text = "尚无方案";
        ErrorText.Text = "";
        SetState(PanePhase.Ready);
    }

    private async Task EnsureDocumentSessionAsync(RevitDocumentData document)
    {
        if (_session?.DocumentFingerprint == document.Fingerprint) return;
        if (_initializing) return;
        _initializing = true;
        _initializationAttempts++;

        SetState(PanePhase.StartingAgent);
        ErrorText.Text = "";
        AgentStatusText.Text = "正在启动 Agent...";
        try
        {
            var repositoryRoot = FindRepositoryRoot();
            var python = Environment.GetEnvironmentVariable("AI_AREA_ASSISTANT_PYTHON") ?? "python";
            var readiness = await new AgentProcess(_agentClient, python, repositoryRoot)
                .EnsureReadyAsync(CancellationToken.None);
            if (!readiness.IsReady) throw new InvalidOperationException(readiness.Message);

            AgentStatusText.Text = readiness.Message;
            var snapshot = new DocumentSnapshot(
                document.RevitInstanceId,
                document.Title,
                document.Path,
                document.Fingerprint,
                new ActiveViewSnapshot(document.ActiveViewId, document.ActiveViewName),
                document.IsModified);
            var binding = await _agentClient.BindDocumentAsync(snapshot, CancellationToken.None);
            if (binding.BindingStatus != "bound" || binding.RvtMcpStatus != "verified")
                throw new InvalidOperationException(binding.PauseReason ?? "Agent 未能验证当前 Revit 文档");

            _generation++;
            _session = await _agentClient.OpenNewSessionAsync(
                Guid.NewGuid().ToString("N"), _generation, document.Fingerprint,
                Path.GetDirectoryName(document.Path) ?? repositoryRoot,
                CancellationToken.None);
            _planning = new PlanningCoordinator(_agentClient);
            _sources = [];
            _initializationAttempts = 0;
            SelectionText.Text = "未选择";
            ResultText.Text = "尚无方案";
            SetState(PanePhase.Ready);
        }
        catch (Exception error)
        {
            AgentStatusText.Text = "连接失败";
            ErrorText.Text = error.Message;
            _nextInitializationAttemptUtc = DateTime.UtcNow.AddSeconds(2);
            SetState(PanePhase.Failed);
        }
        finally
        {
            _initializing = false;
        }
    }

    private void ReadSelectionButton_Click(object sender, System.Windows.RoutedEventArgs e)
    {
        _selectionEvent.Raise();
    }

    private async void ShowSelection(SelectionReadResult result)
    {
        _documentData = result.Document;
        ShowDocument();
        await EnsureDocumentSessionAsync(result.Document);
        if (_session is null) return;
        _sources = result.Sources;
        SelectionText.Text = _sources.Count == 0
            ? "未选择 Floor、Roof 或 Wall"
            : $"已选择 {_sources.Count} 个\n" + string.Join("\n", _sources.Select(source => source.DisplayText));
        SetState(_sources.Count > 0 ? PanePhase.SelectionReady : PanePhase.Ready);
        if (_scanAfterSelection && _sources.Count > 0)
        {
            _scanAfterSelection = false;
            await RunScanAsync();
        }
        else
        {
            _scanAfterSelection = false;
        }
    }

    private void ScanButton_Click(object sender, System.Windows.RoutedEventArgs e)
    {
        if (!_state.CanScan) return;
        _scanAfterSelection = true;
        SetState(PanePhase.Running);
        _selectionEvent.Raise();
    }

    private async Task RunScanAsync()
    {
        if (_session is null || _planning is null) return;
        ResultText.Text = "正在只读扫描并生成方案...";
        ErrorText.Text = "";
        _pollCancellation?.Cancel();
        _pollCancellation = new CancellationTokenSource();
        var cancellationToken = _pollCancellation.Token;
        try
        {
            var ids = string.Join(", ", _sources.Select(source => source.ElementId));
            var snapshot = await _planning.SubmitOnceAsync(
                _session, $"只读扫描当前 Revit 模型，重点分析已选择的边界来源元素：{ids}。生成结构化面积方案。", cancellationToken);
            while (snapshot.State is "queued" or "running")
            {
                await Task.Delay(1000, cancellationToken);
                snapshot = await _planning.RefreshAsync(_session, cancellationToken);
            }
            RenderTerminal(snapshot);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            return;
        }
        catch (Exception error)
        {
            ErrorText.Text = error.Message;
            SetState(PanePhase.Failed);
        }
    }

    private void RenderTerminal(PlanJobSnapshot snapshot)
    {
        if (snapshot.State == "completed" && snapshot.Result is not null)
        {
            ResultText.Text = snapshot.Result.Summary + "\n\n" + snapshot.Result.Question + "\n\n" +
                string.Join("\n\n", snapshot.Result.Options.Select(option =>
                    $"{(option.Recommended ? "[推荐] " : "")}{option.Label}\n{option.Rationale}\n影响：{option.Impact}"));
            SetState(PanePhase.Completed);
            return;
        }
        ErrorText.Text = snapshot.Error is null
            ? $"规划结束：{snapshot.State}"
            : $"{snapshot.Error.Code}: {snapshot.Error.Message}";
        SetState(PanePhase.Failed);
    }

    private void SetState(PanePhase phase)
    {
        _state = new PaneState(phase);
        ReadSelectionButton.IsEnabled = phase is PanePhase.Ready or PanePhase.SelectionReady;
        ScanButton.IsEnabled = _state.CanScan;
    }

    private void ShowDocument()
    {
        if (_documentData is null) return;
        DocumentStatusText.Text = $"{_documentData.Title}\n{_documentData.Path}\n视图：{_documentData.ActiveViewName}\nIsModified：{_documentData.IsModified}";
    }

    private static string FindRepositoryRoot()
    {
        var configured = Environment.GetEnvironmentVariable("AI_AREA_ASSISTANT_REPO_ROOT");
        if (!string.IsNullOrWhiteSpace(configured) && Directory.Exists(Path.Combine(configured, "area_assistant_agent")))
            return configured!;
        var directory = new DirectoryInfo(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)!);
        while (directory is not null)
        {
            if (Directory.Exists(Path.Combine(directory.FullName, "area_assistant_agent"))) return directory.FullName;
            directory = directory.Parent;
        }
        throw new InvalidOperationException("未找到 Python Agent。请设置 AI_AREA_ASSISTANT_REPO_ROOT。");
    }
}
