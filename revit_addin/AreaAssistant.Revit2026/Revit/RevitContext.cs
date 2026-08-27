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

    public static string CreateIdentity(Document document)
    {
        var source = string.IsNullOrWhiteSpace(document.PathName) ? document.Title : document.PathName;
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(source))).ToLowerInvariant();
    }
}
