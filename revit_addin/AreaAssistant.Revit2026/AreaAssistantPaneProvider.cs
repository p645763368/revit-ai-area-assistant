using Autodesk.Revit.UI;

namespace AreaAssistant.Revit2026;

public sealed class AreaAssistantPaneProvider : IDockablePaneProvider
{
    private readonly AreaAssistantPane _pane;

    public AreaAssistantPaneProvider(AreaAssistantPane pane)
    {
        _pane = pane;
    }

    public void SetupDockablePane(DockablePaneProviderData data)
    {
        data.FrameworkElement = _pane;
        data.InitialState = new DockablePaneState
        {
            DockPosition = DockPosition.Right,
        };
    }
}
