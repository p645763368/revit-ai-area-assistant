using System.Windows.Controls;
using Autodesk.Revit.UI;
using AreaAssistant.Revit2026.Revit;

namespace AreaAssistant.Revit2026;

public partial class AreaAssistantPane : Page
{
    private readonly ReadSelectionHandler _selectionHandler;
    private readonly ExternalEvent _selectionEvent;
    private RevitContext? _context;

    public AreaAssistantPane()
    {
        InitializeComponent();
        _selectionHandler = new ReadSelectionHandler(ShowSelection);
        _selectionEvent = ExternalEvent.Create(_selectionHandler);
    }

    internal void Attach(UIApplication application)
    {
        _context = new RevitContext(application);
        var document = _context.CurrentDocument;
        DocumentStatusText.Text = $"{document.Title}\n{document.PathName}\n视图：{document.ActiveView.Name}\nIsModified：{document.IsModified}";
        ReadSelectionButton.IsEnabled = true;
    }

    private void ReadSelectionButton_Click(object sender, System.Windows.RoutedEventArgs e)
    {
        if (_context is null) return;
        _selectionHandler.Request(_context.DocumentIdentity);
        _selectionEvent.Raise();
    }

    private void ShowSelection(SelectionReadResult result)
    {
        if (_context is null || result.DocumentIdentity != _context.DocumentIdentity)
        {
            SelectionText.Text = "文档已变化，请重新读取";
            return;
        }
        SelectionText.Text = result.Sources.Count == 0
            ? "未选择 Floor、Roof 或 Wall"
            : $"已选择 {result.Sources.Count} 个\n" + string.Join("\n", result.Sources.Select(source => source.DisplayText));
        ScanButton.IsEnabled = result.Sources.Count > 0;
    }
}
