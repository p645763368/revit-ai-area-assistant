using System.Reflection;
using Autodesk.Revit.UI;

namespace AreaAssistant.Revit2026;

internal static class AddinIds
{
    internal static readonly Guid PaneGuid = new("42FC2674-60BC-4F90-ACAB-4BB20FC85F18");
}

public sealed class App : IExternalApplication
{
    internal static DockablePaneId PaneId { get; } = new(AddinIds.PaneGuid);

    public Result OnStartup(UIControlledApplication application)
    {
        var pane = new AreaAssistantPane();
        application.RegisterDockablePane(
            PaneId,
            "AI Area Assistant",
            new AreaAssistantPaneProvider(pane));

        var ribbon = application.CreateRibbonPanel("AI Area Assistant");
        var button = new PushButtonData(
            "AreaAssistant.ShowPane",
            "Area\nAssistant",
            Assembly.GetExecutingAssembly().Location,
            typeof(ShowPaneCommand).FullName!);
        ribbon.AddItem(button);
        return Result.Succeeded;
    }

    public Result OnShutdown(UIControlledApplication application)
    {
        return Result.Succeeded;
    }
}
