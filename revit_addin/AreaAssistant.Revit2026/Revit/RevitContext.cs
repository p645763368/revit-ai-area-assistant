using System.IO;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace AreaAssistant.Revit2026.Revit;

public sealed class RevitContext
{
    public RevitContext(UIApplication application) => Application = application;
    public UIApplication Application { get; }
    public Document CurrentDocument => Application.ActiveUIDocument.Document;
    public string DocumentIdentity => CreateIdentity(CurrentDocument);

    public static RevitDocumentData Capture(Document document) => new(
        $"revit-{Process.GetCurrentProcess().Id}",
        document.Title,
        document.PathName,
        CreateIdentity(document),
        document.ActiveView.Id.Value.ToString(),
        document.ActiveView.Name,
        document.IsModified);

    public static string CreateIdentity(Document document)
    {
        return CreateFingerprint(document.PathName, document.Title, document.ProjectInformation.UniqueId);
    }

    public static string CreateFingerprint(string path, string title, string projectInformationId)
    {
        var canonicalPath = string.IsNullOrWhiteSpace(path)
            ? ""
            : Path.GetFullPath(path).Replace('/', '\\').TrimEnd('\\').ToLowerInvariant();
        var source = $"{canonicalPath}|{title}|{projectInformationId}";
        return "sha256:" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(source))).ToLowerInvariant();
    }
}
