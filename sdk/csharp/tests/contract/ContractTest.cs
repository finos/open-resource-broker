// Contract tests for the ORB .NET SDK.
//
// These tests spawn a REAL ORB process over a UNIX domain socket and call
// EVERY method on the client, asserting:
//   1. No 404/405 route-level errors (those indicate a spec/client bug)
//   2. Methods that return data return the expected shape
//   3. Methods that expect missing resources return proper 404-for-resource
//      (not a 404/405 for the route itself)
//
// Distinguish route-level 404/405 from resource-level 404:
//   - A route-level 404/405 means the URL path itself doesn't exist on the server.
//   - A resource-level 404 means the route exists but the resource was not found.
//   - Route-level 404/405 → SDK bug. Resource-level 404 → expected behavior.

using FINOS.OpenResourceBroker;
using FINOS.OpenResourceBroker.Sse;
using OpenResourceBroker.Sdk.Model;
using Xunit;
using Xunit.Abstractions;

namespace ContractTests;

[Collection("OrbContract")]
public class ContractTest : IClassFixture<OrbFixture>
{
    private readonly OrbFixture _fixture;
    private readonly ITestOutputHelper _out;

    public ContractTest(OrbFixture fixture, ITestOutputHelper output)
    {
        _fixture = fixture;
        _out = output;
    }

    // ---------------------------------------------------------------------------
    // Helper: assert that an exception is NOT a route-level 404/405
    // ---------------------------------------------------------------------------

    private void AssertNotRouteLevelError(Exception ex, string context)
    {
        if (ex is not OrbApiException apiEx) return;

        // 405 is always a route-level error.
        if (apiEx.StatusCode == 405)
            throw new Exception($"{context}: got HTTP 405 Method Not Allowed — route-level bug in the client/spec");

        // Distinguish a route-level 404 from a resource-level 404.  The orb
        // returns a resource-level 404 as {"detail":"<Resource> not found"} and a
        // route-level 404 (unmatched path) as FastAPI's generic
        // {"detail":"Not Found"} — the SAME JSON shape.  The ONLY reliable
        // discriminator is the exact generic detail string (title-case "Not
        // Found"), NOT the presence/absence of a body or the substring "detail"
        // (every real 404 body contains "detail").
        if (apiEx.StatusCode == 404 && IsRouteLevelNotFound(apiEx))
            throw new Exception($"{context}: got HTTP 404 with generic 'Not Found' detail — route-level missing path bug");
    }

    // True if a 404 is FastAPI's route-level "Not Found" (unknown path) rather
    // than a resource-level not-found.  Matches the generic detail exactly
    // (title-case), which is what FastAPI emits for an unmatched route.
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
            using var doc = System.Text.Json.JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("detail", out var detail) &&
                detail.ValueKind == System.Text.Json.JsonValueKind.String)
                return detail.GetString();
        }
        catch { }
        return null;
    }

    // ---------------------------------------------------------------------------
    // System / Observability
    // ---------------------------------------------------------------------------

    [Fact]
    public async Task Health_Returns_HealthyOrDegraded()
    {
        // ORB may return 200 (healthy) or 503 (degraded) — both are acceptable.
        // The HealthAsync method accepts both status codes.
        var result = await _fixture.Client.HealthAsync();
        Assert.NotNull(result);
        Assert.True(result.ContainsKey("status"), "health response must have 'status' key");
        var status = result["status"]?.ToString() ?? "";
        var validStatuses = new[] { "healthy", "degraded", "unhealthy" };
        Assert.True(validStatuses.Contains(status), $"health status '{status}' is not in {string.Join(",", validStatuses)}");
        _out.WriteLine($"  health: {status}");
    }

    [Fact]
    public async Task Info_Returns_Object()
    {
        var result = await _fixture.Client.InfoAsync();
        Assert.NotNull(result);
        _out.WriteLine($"  info keys: {string.Join(", ", result.Keys)}");
    }

    [Fact]
    public async Task Metrics_Returns_PrometheusText()
    {
        // Metrics may be empty if the prometheus-client monitoring extra is not installed.
        // We just verify the route EXISTS (no 404/405) — the response may be empty.
        try
        {
            var result = await _fixture.Client.MetricsAsync();
            Assert.NotNull(result);
            _out.WriteLine($"  metrics: {result.Length} bytes");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "Metrics");
        }
    }

    [Fact]
    public async Task GetDashboardSummary_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetDashboardSummaryAsync();
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetDashboardSummary");
        }
    }

    [Fact]
    public async Task GetTelemetryStatus_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetTelemetryStatusAsync();
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetTelemetryStatus");
        }
    }

    [Fact]
    public async Task GetMe_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetMeAsync();
            Assert.NotNull(result);
            _out.WriteLine($"  me: {System.Text.Json.JsonSerializer.Serialize(result)}");
        }
        catch (OrbApiException ex)
        {
            // 401 is expected when no auth session is active.
            // Note: 200 cannot reach this catch block (success never throws OrbApiException),
            // so the only acceptable exception status is 401.
            AssertNotRouteLevelError(ex, "GetMe");
            Assert.Equal(401, ex.StatusCode);
        }
    }

    // ---------------------------------------------------------------------------
    // Providers
    // ---------------------------------------------------------------------------

    [Fact]
    public async Task ListProviders_Returns_ProvidersArray()
    {
        var result = await _fixture.Client.ListProvidersAsync();
        Assert.NotNull(result);
        Assert.True(result.ContainsKey("providers"), "response must have 'providers' key");
        _out.WriteLine($"  providers key present: true");
    }

    [Fact]
    public async Task GetAllProviderSchemas_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetAllProviderSchemasAsync();
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetAllProviderSchemas");
        }
    }

    [Fact]
    public async Task GetProviderSchema_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetProviderSchemaAsync("aws");
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetProviderSchema(aws)");
        }
    }

    [Fact]
    public async Task GetProvidersHealth_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetProvidersHealthAsync();
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetProvidersHealth");
        }
    }

    // ---------------------------------------------------------------------------
    // Templates
    // ---------------------------------------------------------------------------

    private string? _createdTemplateId;

    [Fact]
    public async Task ListTemplates_Returns_TemplatesArray()
    {
        var result = await _fixture.Client.ListTemplatesAsync();
        Assert.NotNull(result);
        Assert.NotNull(result.Templates);
        _out.WriteLine($"  templates: {result.Templates.Count}");
    }

    [Fact]
    public async Task CreateTemplate_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.CreateTemplateAsync(new TemplateCreateRequest(
                "contract-test-template-" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
            {
                Name = "contract-test-template",
                Description = "Created by .NET contract test",
            });
            Assert.NotNull(result);
            if (result.TemplateId != null)
            {
                _createdTemplateId = result.TemplateId;
                _out.WriteLine($"  created template: {_createdTemplateId}");
            }
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "CreateTemplate");
            _out.WriteLine($"  createTemplate returned error (acceptable): {ex.StatusCode}");
        }
    }

    [Fact]
    public async Task GetTemplate_NonExistent_Returns_ResourceLevel404()
    {
        // The server returns 404 for a nonexistent template — confirm the route exists
        // (any 404 with a response body means FastAPI routed to the handler).
        try
        {
            await _fixture.Client.GetTemplateAsync("nonexistent-template-id-xyz");
            // If no exception: server returned synthetic data — route exists
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetTemplate(nonexistent)");
            // 404 (resource not found) or 200 (synthetic data) both mean route exists
            Assert.True(ex.StatusCode is 404 or 422,
                $"Expected 404 or 422, got {ex.StatusCode}");
            _out.WriteLine($"  getTemplate(nonexistent) → {ex.StatusCode} (route exists)");
        }
    }

    [Fact]
    public async Task ValidateTemplate_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.ValidateTemplateAsync(new { name = "test", provider_type = "aws", config = new { } });
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "ValidateTemplate");
        }
    }

    [Fact]
    public async Task RefreshTemplates_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.RefreshTemplatesAsync();
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "RefreshTemplates");
        }
    }

    [Fact]
    public async Task GenerateTemplates_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.GenerateTemplatesAsync(new GenerateTemplatesBody
            {
                Provider = "aws-stub",
                AllProviders = false,
            });
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GenerateTemplates");
        }
    }

    [Fact]
    public async Task UpdateTemplate_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.UpdateTemplateAsync("nonexistent-xyz", new TemplateUpdateRequest
            {
                Name = "updated",
                Description = "updated",
            });
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "UpdateTemplate(nonexistent)");
            Assert.True(ex.StatusCode is 404 or 403 or 422,
                $"Expected 404/403/422, got {ex.StatusCode}");
        }
    }

    [Fact]
    public async Task DeleteTemplate_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.DeleteTemplateAsync("nonexistent-xyz");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "DeleteTemplate(nonexistent)");
            Assert.True(ex.StatusCode is 404 or 403,
                $"Expected 404 or 403, got {ex.StatusCode}");
        }
    }

    // ---------------------------------------------------------------------------
    // Machines
    // ---------------------------------------------------------------------------

    [Fact]
    public async Task ListMachines_Returns_MachinesArray()
    {
        var result = await _fixture.Client.ListMachinesAsync();
        Assert.NotNull(result);
        Assert.NotNull(result.Machines);
        _out.WriteLine($"  machines: {result.Machines.Count}");
    }

    [Fact]
    public async Task GetMachine_NonExistent_Returns_ResourceLevel404()
    {
        try
        {
            await _fixture.Client.GetMachineAsync("nonexistent-machine-id-xyz");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetMachine(nonexistent)");
            Assert.Equal(404, ex.StatusCode);
            _out.WriteLine($"  getMachine(nonexistent) → 404 (correct)");
        }
    }

    [Fact]
    public async Task RequestMachines_DoesNotReturn_RouteError()
    {
        // Ensure POST /api/v1/machines/request is always exercised — never silently skipped.
        // Strategy: use an existing template if any, otherwise try to create one;
        // if creation is rejected (e.g. 403 insufficient permissions), fall back to a
        // synthetic ID. In all cases the request MUST reach the route and must not return
        // a route-level 404/405. A resource-level error (400/403/404/422/500/503) is fine.
        var templates = await _fixture.Client.ListTemplatesAsync();
        string templateId;

        if (templates.Templates.Count > 0)
        {
            templateId = templates.Templates[0].TemplateId ?? "unknown";
            _out.WriteLine($"  using existing template: {templateId}");
        }
        else
        {
            // Try to create a template; accept failure with any non-route-level error.
            string? createdId = null;
            try
            {
                var created = await _fixture.Client.CreateTemplateAsync(new TemplateCreateRequest(
                    "contract-req-machines-" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
                {
                    Name = "contract-request-machines",
                    Description = "Temporary template created by RequestMachines contract test",
                });
                createdId = created.TemplateId;
                _out.WriteLine($"  created template for requestMachines: {createdId}");
            }
            catch (OrbApiException ex)
            {
                AssertNotRouteLevelError(ex, "CreateTemplate(for RequestMachines)");
                _out.WriteLine($"  createTemplate returned {ex.StatusCode} — using synthetic template ID");
            }

            // Whether creation succeeded or failed, use the ID (real or synthetic).
            // With a synthetic ID, RequestMachines will return a resource-level 404/422/400.
            templateId = createdId ?? "contract-test-nonexistent-template-id";
        }

        // Always call POST /api/v1/machines/request — route must exist.
        try
        {
            var result = await _fixture.Client.RequestMachinesAsync(new RequestMachinesRequest(templateId, 1));
            Assert.NotNull(result);
            _out.WriteLine($"  requestMachines → success (requestId={result.RequestId})");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "RequestMachines");
            // Resource-level errors are acceptable; route-level 404/405 is not.
            Assert.True(ex.StatusCode is 400 or 403 or 404 or 422 or 500 or 503,
                $"Expected resource-level error (400/403/404/422/500/503), got {ex.StatusCode}");
            _out.WriteLine($"  requestMachines → resource-level error {ex.StatusCode} (route exists, acceptable)");
        }
    }

    [Fact]
    public async Task ReturnMachines_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.ReturnMachinesAsync(new ReturnMachinesRequest
            {
                MachineIds = ["nonexistent-machine-id"],
            });
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "ReturnMachines");
        }
    }

    [Fact]
    public async Task SyncMachineStatus_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.SyncMachineStatusAsync("nonexistent-machine-id");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "SyncMachineStatus");
            Assert.Equal(404, ex.StatusCode);
        }
    }

    [Fact]
    public async Task GetMachineMetrics_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.GetMachineMetricsAsync("nonexistent-machine-id");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetMachineMetrics");
            Assert.Equal(404, ex.StatusCode);
        }
    }

    [Fact]
    public async Task PurgeMachine_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.PurgeMachineAsync("nonexistent-machine-id");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "PurgeMachine");
            Assert.True(ex.StatusCode is 404 or 403,
                $"Expected 404 or 403, got {ex.StatusCode}");
        }
    }

    // ---------------------------------------------------------------------------
    // Requests
    // ---------------------------------------------------------------------------

    [Fact]
    public async Task ListRequests_Returns_RequestsArray()
    {
        var result = await _fixture.Client.ListRequestsAsync();
        Assert.NotNull(result);
        Assert.NotNull(result.Requests);
        _out.WriteLine($"  requests: {result.Requests.Count}");
    }

    [Fact]
    public async Task ListReturnRequests_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.ListReturnRequestsAsync();
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "ListReturnRequests");
        }
    }

    [Fact]
    public async Task GetRequestStatus_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetRequestStatusAsync("nonexistent-request-id-xyz");
            Assert.NotNull(result);
            _out.WriteLine($"  getRequestStatus(nonexistent) → 200 with synthetic data");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetRequestStatus(nonexistent)");
            Assert.True(ex.StatusCode is 404 or 400,
                $"Expected 404 or 400, got {ex.StatusCode}");
        }
    }

    [Fact]
    public async Task GetRequestTimeline_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.GetRequestTimelineAsync("nonexistent-request-id-xyz");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetRequestTimeline(nonexistent)");
            Assert.Equal(404, ex.StatusCode);
        }
    }

    [Fact]
    public async Task BatchGetRequestStatus_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.BatchGetRequestStatusAsync(
                new BatchRequestStatusBody(["nonexistent-id-1", "nonexistent-id-2"]));
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "BatchGetRequestStatus");
        }
    }

    [Fact]
    public async Task CancelRequest_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.CancelRequestAsync("nonexistent-request-id-xyz");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "CancelRequest(nonexistent)");
            Assert.True(ex.StatusCode is 404 or 403,
                $"Expected 404 or 403, got {ex.StatusCode}");
        }
    }

    [Fact]
    public async Task PurgeRequest_NonExistent_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.PurgeRequestAsync("nonexistent-request-id-xyz");
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "PurgeRequest(nonexistent)");
            Assert.True(ex.StatusCode is 404 or 403,
                $"Expected 404 or 403, got {ex.StatusCode}");
        }
    }

    [Fact]
    public async Task StreamRequestStatus_NonExistent_DoesNotReturn_RouteError()
    {
        using var cts = new CancellationTokenSource(10_000);
        var caughtEx = (OrbApiException?)null;
        var events = 0;

        try
        {
            await foreach (var ev in _fixture.Client.StreamRequestStatusAsync(
                "nonexistent-request-id-xyz",
                intervalSeconds: 1,
                timeoutSeconds: 2,
                ct: cts.Token))
            {
                events++;
                _out.WriteLine($"  streamRequestStatus event: {ev.Status}");
            }
        }
        catch (OrbApiException ex)
        {
            caughtEx = ex;
        }
        catch (OperationCanceledException)
        {
            // timeout — acceptable
        }

        if (caughtEx != null)
        {
            AssertNotRouteLevelError(caughtEx, "StreamRequestStatus(nonexistent)");
            _out.WriteLine($"  streamRequestStatus(nonexistent) → error (route exists): {caughtEx.StatusCode}");
        }
        else
        {
            _out.WriteLine($"  streamRequestStatus(nonexistent) → {events} events (route exists)");
        }
    }

    // ---------------------------------------------------------------------------
    // SSE event stream
    // ---------------------------------------------------------------------------

    [Fact]
    public async Task StreamEvents_ConnectAndAbort_DoesNotReturn_RouteError()
    {
        using var cts = new CancellationTokenSource(3_000);
        var frames = new List<SseFrame>();

        try
        {
            await foreach (var frame in _fixture.Client.StreamEventsAsync(cts.Token))
            {
                frames.Add(frame);
                cts.Cancel(); // Abort after first event
                break;
            }
        }
        catch (OperationCanceledException) { /* expected */ }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "StreamEvents");
        }

        _out.WriteLine($"  streamEvents: connected, got {frames.Count} frames before abort");
    }

    // ---------------------------------------------------------------------------
    // Config
    // ---------------------------------------------------------------------------

    [Fact]
    public async Task GetFullConfig_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetFullConfigAsync();
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetFullConfig");
        }
    }

    [Fact]
    public async Task GetConfigSources_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetConfigSourcesAsync();
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetConfigSources");
        }
    }

    [Fact]
    public async Task GetConfigValue_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.GetConfigValueAsync("server.port");
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "GetConfigValue(server.port)");
        }
    }

    [Fact]
    public async Task ValidateConfig_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.ValidateConfigAsync();
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "ValidateConfig");
        }
    }

    [Fact]
    public async Task SaveConfig_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.SaveConfigAsync(new SaveRequest());
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "SaveConfig");
        }
    }

    [Fact]
    public async Task SetConfigValue_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.SetConfigValueAsync("logging.level", new SetValueRequest { Value = "ERROR" });
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "SetConfigValue");
        }
    }

    // ---------------------------------------------------------------------------
    // Admin
    // ---------------------------------------------------------------------------

    [Fact]
    public async Task InitOrb_DoesNotReturn_RouteError()
    {
        try
        {
            var result = await _fixture.Client.InitOrbAsync(new InitBody
            {
                Confirm = "false",
                Force = false,
                GenerateTemplates = false,
            });
            Assert.NotNull(result);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "InitOrb");
        }
    }

    [Fact]
    public async Task CleanupDatabase_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.CleanupDatabaseAsync(new CleanupDatabaseBody
            {
                Confirm = "false",
                OlderThanDays = 999,
            });
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "CleanupDatabase");
        }
    }

    [Fact]
    public async Task ReloadConfig_DoesNotReturn_RouteError()
    {
        try
        {
            await _fixture.Client.ReloadConfigAsync();
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "ReloadConfig");
        }
    }

    [Fact]
    public async Task WipeDatabase_ConfirmFalse_DoesNotReturn_RouteError()
    {
        // confirm: false — do NOT actually wipe
        try
        {
            await _fixture.Client.WipeDatabaseAsync(confirm: false);
        }
        catch (OrbApiException ex)
        {
            AssertNotRouteLevelError(ex, "WipeDatabase(confirm:false)");
        }
    }
}
