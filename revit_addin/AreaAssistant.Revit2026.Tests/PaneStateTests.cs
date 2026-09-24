using AreaAssistant.Revit2026.Planning;
using Xunit;

namespace AreaAssistant.Revit2026.Tests;

public sealed class PaneStateTests
{
    [Theory]
    [InlineData(PanePhase.StartingAgent, false)]
    [InlineData(PanePhase.Ready, false)]
    [InlineData(PanePhase.SelectionReady, true)]
    [InlineData(PanePhase.Running, false)]
    [InlineData(PanePhase.Completed, false)]
    [InlineData(PanePhase.Failed, false)]
    public void ScanIsOnlyEnabledWithAReadySelection(PanePhase phase, bool expected) =>
        Assert.Equal(expected, new PaneState(phase).CanScan);
}
