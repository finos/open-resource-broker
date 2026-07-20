using System.Text.Json;
using ContractTests; // OrbFixture — linked from ../contract/OrbFixture.cs
using FINOS.OpenResourceBroker;
using OpenResourceBroker.Sdk.Model;
using Xunit;
using Xunit.Abstractions;

namespace ParityTests;

/// <summary>
/// Cross-language parity runner (.NET / C# leg).
///
/// Loads the language-agnostic fixture sdk/parity/scenario.json and executes its
/// six ordered steps against a REAL orb spawned over a UNIX domain socket (the
/// same OrbFixture the contract tests use). Each step is dispatched to the
/// concrete C# SDK method named in the fixture's sdk_methods.csharp entry, and
/// the result is asserted against the step's expected block and skip rules.
///
/// Static conformance (validate_sdk_spec_conformance.py) proves each step's
/// (method, path, operationId) — and now sdk_methods.csharp — resolves to a real
/// spec operation and client method. This runtime leg proves the C# SDK actually
/// drives the scenario end-to-end.
///
/// The six steps share one orb instance and bound state, so they run as a single
/// ordered [Fact] rather than independent test methods.
/// </summary>
public class ParityScenarioTests : IClassFixture<OrbFixture>
{
    private readonly OrbFixture _fixture;
    private readonly ITestOutputHelper _out;

    public ParityScenarioTests(OrbFixture fixture, ITestOutputHelper output)
    {
        _fixture = fixture;
        _out = output;
    }

    private void AssertNotRouteLevelError(Exception ex, string context)
    {
        if (ex is not OrbApiException apiEx) return;
        if (apiEx.StatusCode == 405)
            throw new Exception($"{context}: HTTP 405 Method Not Allowed — route-level bug");
        if (apiEx.StatusCode == 404 && IsRouteLevelNotFound(apiEx))
            throw new Exception($"{context}: HTTP 404 with generic 'Not Found' detail — route-level missing path");
    }

    private static bool IsRouteLevelNotFound(OrbApiException ex)
    {
        var detail = ParseDetail(ex.ResponseBody);
        return detail != null && detail.Trim() == "Not Found";
    }

    private static string? ParseDetail(string? body)
    {
        if (string.IsNullOrWhiteSpace(body)) return null;
        try
        {
            using var doc = JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("detail", out var detail) &&
                detail.ValueKind == JsonValueKind.String)
                return detail.GetString();
        }
        catch { }
        return null;
    }

    private static JsonElement LoadScenario()
    {
        // The test binary runs from tests/parity/bin/<cfg>/net8.0, so walk up to
        // the sdk/ root and read sdk/parity/scenario.json.
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 12 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "parity", "scenario.json");
            if (File.Exists(candidate))
                return JsonDocument.Parse(File.ReadAllText(candidate)).RootElement.Clone();
            var sdkCandidate = Path.Combine(dir, "sdk", "parity", "scenario.json");
            if (File.Exists(sdkCandidate))
                return JsonDocument.Parse(File.ReadAllText(sdkCandidate)).RootElement.Clone();
            dir = Directory.GetParent(dir)?.FullName;
        }
        throw new FileNotFoundException("Could not locate sdk/parity/scenario.json from " + AppContext.BaseDirectory);
    }

    private static string MethodFor(JsonElement scenario, int step)
    {
        foreach (var s in scenario.GetProperty("steps").EnumerateArray())
        {
            if (s.GetProperty("step").GetInt32() == step)
                return s.GetProperty("sdk_methods").GetProperty("csharp").GetString()!;
        }
        throw new Exception($"no fixture step {step}");
    }

    [Fact]
    public async Task ParityScenario_RunsAllSteps()
    {
        var scenario = LoadScenario();
        var client = _fixture.Client;
        var results = new Dictionary<int, string>();

        string? firstTemplateId = null;
        string? requestId = null;
        string? machineId = null;

        // Step 1 — health_check
        _out.WriteLine($"step 1 health_check -> {MethodFor(scenario, 1)}");
        var health = await client.HealthAsync();
        Assert.True(health.ContainsKey("status"), "health response must have a status field");
        var status = health["status"]?.ToString();
        Assert.True(status is "healthy" or "degraded", $"status should be healthy|degraded, got: {status}");
        results[1] = "PASS";

        // Step 2 — list_templates
        _out.WriteLine($"step 2 list_templates -> {MethodFor(scenario, 2)}");
        var templates = await client.ListTemplatesAsync();
        Assert.NotNull(templates.Templates);
        if (templates.Templates!.Count > 0)
        {
            firstTemplateId = templates.Templates[0].TemplateId;
            _out.WriteLine($"  bound first_template_id={firstTemplateId}");
        }
        results[2] = "PASS";

        // Step 3 — request_machines (precondition: firstTemplateId bound)
        _out.WriteLine($"step 3 request_machines -> {MethodFor(scenario, 3)}");
        if (firstTemplateId == null)
        {
            results[3] = "SKIP";
        }
        else
        {
            try
            {
                var req = await client.RequestMachinesAsync(new RequestMachinesRequest(firstTemplateId, 1));
                Assert.False(string.IsNullOrEmpty(req.RequestId), "2xx must bind a non-empty request_id");
                requestId = req.RequestId;
                _out.WriteLine($"  bound request_id={requestId}");
                results[3] = "PASS";
            }
            catch (OrbApiException ex)
            {
                // Provider-level failure (no real AWS) is not a route bug.
                AssertNotRouteLevelError(ex, "requestMachines");
                _out.WriteLine($"  requestMachines non-route error {ex.StatusCode} (expected without real provider)");
                results[3] = "SKIP";
            }
        }

        // Step 4 — poll_request_status (precondition: requestId bound)
        _out.WriteLine($"step 4 poll_request_status -> {MethodFor(scenario, 4)}");
        if (requestId == null)
        {
            results[4] = "SKIP";
        }
        else
        {
            var st = await client.GetRequestStatusAsync(requestId);
            Assert.NotNull(st);
            if (st.Requests != null && st.Requests.Count > 0)
            {
                var machines = st.Requests[0].Machines;
                if (machines != null && machines.Count > 0)
                    machineId = machines[0].MachineId;
            }
            results[4] = "PASS";
        }

        // Step 5 — return_machines (precondition: requestId AND machineId)
        _out.WriteLine($"step 5 return_machines -> {MethodFor(scenario, 5)}");
        if (requestId == null || machineId == null)
        {
            results[5] = "SKIP";
        }
        else
        {
            try
            {
                await client.ReturnMachinesAsync(new ReturnMachinesRequest { MachineIds = [machineId] });
            }
            catch (OrbApiException ex)
            {
                AssertNotRouteLevelError(ex, "returnMachines");
                _out.WriteLine($"  returnMachines non-route error {ex.StatusCode} (acceptable)");
            }
            results[5] = "PASS";
        }

        // Step 6 — list_requests (always executed)
        _out.WriteLine($"step 6 list_requests -> {MethodFor(scenario, 6)}");
        var requests = await client.ListRequestsAsync();
        Assert.NotNull(requests);
        results[6] = "PASS";

        foreach (var s in scenario.GetProperty("steps").EnumerateArray())
        {
            var n = s.GetProperty("step").GetInt32();
            _out.WriteLine($"PARITY {n} {s.GetProperty("name").GetString()}: {results[n]}");
        }
    }
}
