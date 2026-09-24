namespace AreaAssistant.Revit2026.Planning;

public enum PanePhase { StartingAgent, Ready, SelectionReady, Running, Completed, Failed }

public sealed record PaneState(PanePhase Phase)
{
    public bool CanScan => Phase == PanePhase.SelectionReady;
}
