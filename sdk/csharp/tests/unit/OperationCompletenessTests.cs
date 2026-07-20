// Static operation-completeness check (.NET / C# leg).
//
// Ports the intent of the Go SDK's conformance_test.go to C# as a pure unit
// check (no ORB process needed): it enumerates every operationId declared in
// sdk/spec/openapi.json and asserts the hand-written client (OrbClient.cs)
// covers each one. Every client method documents its operationId in an XML doc
// comment (the same convention validate_sdk_spec_conformance.py relies on), so a
// missing operation — e.g. a brand-new endpoint added to the spec — leaves its
// operationId absent from the client source and fails this test.
//
// The net effect is the cross-language guarantee: a new spec endpoint now fails
// CI in every language's completeness test, not only Go's.

using System.Text.Json;
using Xunit;

namespace UnitTests;

public class OperationCompletenessTests
{
    private static readonly HashSet<string> HttpMethods =
        new(StringComparer.OrdinalIgnoreCase) { "get", "post", "put", "delete", "patch", "head" };

    // The test binary runs from tests/unit/bin/<cfg>/net8.0, so walk up until the
    // directory that contains spec/openapi.json (the sdk/ root) — the same
    // resolution strategy the parity runner uses for scenario.json.
    private static string LocateSdkRoot()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 12 && dir != null; i++)
        {
            if (File.Exists(Path.Combine(dir, "spec", "openapi.json")))
                return dir;
            if (File.Exists(Path.Combine(dir, "sdk", "spec", "openapi.json")))
                return Path.Combine(dir, "sdk");
            dir = Directory.GetParent(dir)?.FullName;
        }
        throw new FileNotFoundException(
            "Could not locate sdk/spec/openapi.json from " + AppContext.BaseDirectory);
    }

    private static List<string> SpecOperationIds()
    {
        var specPath = Path.Combine(LocateSdkRoot(), "spec", "openapi.json");
        using var doc = JsonDocument.Parse(File.ReadAllText(specPath));
        var ids = new List<string>();
        foreach (var pathEntry in doc.RootElement.GetProperty("paths").EnumerateObject())
        {
            foreach (var methodEntry in pathEntry.Value.EnumerateObject())
            {
                if (!HttpMethods.Contains(methodEntry.Name)) continue;
                if (methodEntry.Value.TryGetProperty("operationId", out var opId) &&
                    opId.ValueKind == JsonValueKind.String)
                {
                    var value = opId.GetString();
                    if (!string.IsNullOrEmpty(value)) ids.Add(value!);
                }
            }
        }
        return ids;
    }

    [Fact]
    public void SpecDeclaresExactly45Operations()
    {
        // Mirrors Go's `if len(ops) != 45` sentinel: a spec that grows or shrinks
        // forces a deliberate update rather than silently under-covering.
        Assert.Equal(45, SpecOperationIds().Count);
    }

    [Fact]
    public void ClientCoversEverySpecOperation()
    {
        // LocateSdkRoot returns the sdk/ root (the dir holding spec/); the C#
        // client lives under sdk/csharp/src/.
        var root = LocateSdkRoot();
        var clientPath = Path.Combine(
            root, "csharp", "src", "FINOS.OpenResourceBroker", "OrbClient.cs");
        Assert.True(File.Exists(clientPath), "client not found: " + clientPath);
        var clientSource = File.ReadAllText(clientPath);

        var missing = SpecOperationIds().Where(id => !clientSource.Contains(id)).ToList();
        Assert.True(missing.Count == 0,
            $"OrbClient.cs does not cover {missing.Count} spec operation(s): {string.Join(", ", missing)}");
    }
}
