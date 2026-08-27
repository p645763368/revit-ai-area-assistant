using System.Text.Json.Serialization;

namespace AreaAssistant.Revit2026.Revit;

public sealed record PointMm(double X, double Y, double Z);

public sealed record SelectedSource(
    [property: JsonPropertyName("element_id")] long ElementId,
    [property: JsonPropertyName("unique_id")] string UniqueId,
    string Category,
    [property: JsonPropertyName("type_name")] string TypeName,
    [property: JsonPropertyName("level_name")] string LevelName,
    [property: JsonPropertyName("bounds_min_mm")] PointMm BoundsMinMm,
    [property: JsonPropertyName("bounds_max_mm")] PointMm BoundsMaxMm,
    [property: JsonPropertyName("geometry_mm")] IReadOnlyList<PointMm> GeometryMm)
{
    [JsonIgnore]
    public string DisplayText => $"ID {ElementId} | {Category} | {LevelName} | {TypeName}";
}

public sealed record SelectionReadResult(string DocumentIdentity, IReadOnlyList<SelectedSource> Sources);
