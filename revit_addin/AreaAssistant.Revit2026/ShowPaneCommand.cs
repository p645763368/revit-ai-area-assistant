using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace AreaAssistant.Revit2026;

[Transaction(TransactionMode.Manual)]
public sealed class ShowPaneCommand : IExternalCommand
{
    public Result Execute(
        ExternalCommandData commandData,
        ref string message,
        ElementSet elements)
    {
        commandData.Application.GetDockablePane(App.PaneId).Show();
        return Result.Succeeded;
    }
}
