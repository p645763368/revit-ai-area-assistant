using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace AreaAssistant.Revit2026.Revit;

public sealed class ReadSelectionHandler : IExternalEventHandler
{
    private const double MillimetresPerFoot = 304.8;
    private readonly Action<SelectionReadResult> _completed;

    public ReadSelectionHandler(Action<SelectionReadResult> completed) => _completed = completed;
    public string GetName() => "Read AI Area Assistant selection";

    public void Execute(UIApplication application)
    {
        var document = application.ActiveUIDocument.Document;
        var sources = application.ActiveUIDocument.Selection.GetElementIds()
            .Select(document.GetElement)
            .Where(element => element is Floor or RoofBase or Wall)
            .Select(element => Map(document, element))
            .ToArray();
        _completed(new SelectionReadResult(RevitContext.Capture(document), sources));
    }

    private static SelectedSource Map(Document document, Element element)
    {
        var bounds = element.get_BoundingBox(null) ?? throw new InvalidOperationException($"Element {element.Id.Value} has no bounds.");
        var levelName = element.LevelId == ElementId.InvalidElementId ? "<无楼层>" : document.GetElement(element.LevelId)?.Name ?? "<无楼层>";
        var typeName = document.GetElement(element.GetTypeId())?.Name ?? "<无类型>";
        var geometry = element.Location is LocationCurve location
            ? new[] { ToMm(location.Curve.GetEndPoint(0)), ToMm(location.Curve.GetEndPoint(1)) }
            : new[] { ToMm(bounds.Min), ToMm(bounds.Max) };
        return new SelectedSource(
            element.Id.Value,
            element.UniqueId,
            element.Category?.Name ?? element.GetType().Name,
            typeName,
            levelName,
            ToMm(bounds.Min),
            ToMm(bounds.Max),
            geometry);
    }

    private static PointMm ToMm(XYZ point) => new(
        Math.Round(point.X * MillimetresPerFoot, 1),
        Math.Round(point.Y * MillimetresPerFoot, 1),
        Math.Round(point.Z * MillimetresPerFoot, 1));
}
