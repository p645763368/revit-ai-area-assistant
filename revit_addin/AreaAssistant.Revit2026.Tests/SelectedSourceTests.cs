using System.Text.Json;
using AreaAssistant.Revit2026.Revit;
using Xunit;

namespace AreaAssistant.Revit2026.Tests;

public sealed class SelectedSourceTests
{
    [Fact]
    public void DocumentFingerprintMatchesAgentShape()
    {
        var fingerprint = RevitContext.CreateFingerprint("D:\\Project\\Model.rvt", "Model", "project-uid");

        Assert.Equal("sha256:a21ad434058f0abc31f643ce5a422aa7ae91699faed3e99b364b89fd406049ec", fingerprint);
        Assert.Equal(fingerprint, RevitContext.CreateFingerprint("d:/project/model.rvt", "Model", "project-uid"));
    }

    [Fact]
    public void SerializesStableReadOnlySelectionData()
    {
        var source = new SelectedSource(42, "uid", "Walls", "Exterior", "Level 1",
            new PointMm(0, 0, 0), new PointMm(1000, 200, 3000),
            [new PointMm(0, 0, 0), new PointMm(1000, 0, 0)]);

        var json = JsonSerializer.Serialize(source);

        Assert.Contains("\"element_id\":42", json);
        Assert.Contains("\"unique_id\":\"uid\"", json);
        Assert.Contains("\"bounds_min_mm\"", json);
        Assert.Contains("\"geometry_mm\"", json);
        Assert.DoesNotContain("DisplayText", json);
    }
}
