// ORB .NET SDK — Public Client
//
// Covers all 44 operations from sdk/spec/openapi.json.
// Uses the generated OpenResourceBroker.Sdk.Model types for typed
// request/response shapes (hybrid model — see sdk/ARCHITECTURE.md).  The
// generated models are System.Text.Json-native and register their per-type
// converters via reflection in JsonOpts below.
//
// Two operating modes:
//   - spawn: client starts ORB as a child process (UDS transport)
//   - remote: client connects to an existing ORB instance (TCP/HTTPS)

using System.Net.Http.Headers;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using FINOS.OpenResourceBroker.Auth;
using FINOS.OpenResourceBroker.Process;
using FINOS.OpenResourceBroker.Sse;
using FINOS.OpenResourceBroker.Transport;
using OpenResourceBroker.Sdk.Model;

namespace FINOS.OpenResourceBroker;

// ---------------------------------------------------------------------------
// Client configuration
// ---------------------------------------------------------------------------

/// <summary>Configuration for <see cref="OrbClient"/>.</summary>
public sealed class ClientConfig
{
    /// <summary>Base URL for remote mode (default: http://localhost:8000).</summary>
    public string? BaseUrl { get; init; }

    /// <summary>Authentication strategy (default: none).</summary>
    public AuthOption Auth { get; init; } = AuthOption.None;

    /// <summary>HTTP timeout in milliseconds (default: 30_000).</summary>
    public int TimeoutMs { get; init; } = 30_000;

    /// <summary>Retry configuration.</summary>
    public RetryConfig? Retry { get; init; }

    /// <summary>If set, start and manage an ORB subprocess (UDS transport).</summary>
    public ProcessConfig? Process { get; init; }

    /// <summary>UNIX socket path for UDS mode without a managed subprocess.</summary>
    public string? SocketPath { get; init; }

    /// <summary>
    /// Scheduler backend.  Sends the <c>X-ORB-Scheduler</c> header when not
    /// <see cref="Scheduler.Default"/>.  A typed enum (rather than a raw string)
    /// prevents a typo from silently sending a wrong header value.
    /// </summary>
    public Scheduler Scheduler { get; init; } = Scheduler.Default;
}

// ---------------------------------------------------------------------------
// OrbClient
// ---------------------------------------------------------------------------

/// <summary>
/// FINOS Open Resource Broker SDK client.
/// Covers all 44 operations from the ORB API.
/// </summary>
public sealed class OrbClient : IAsyncDisposable
{
    private readonly HttpClient _http;
    private readonly string _baseUrl;
    private readonly Scheduler _scheduler;
    private SubprocessManager? _proc;

    /// <summary>
    /// System.Text.Json options for the hybrid client.  The generated
    /// OpenResourceBroker.Sdk models are STJ-native but each ships a bespoke
    /// <see cref="JsonConverter{T}"/>; we auto-register every converter in the
    /// generated model assembly by reflection so a spec change that adds a model
    /// needs no code change here.
    /// </summary>
    private static readonly JsonSerializerOptions JsonOpts = BuildJsonOptions();

    private static JsonSerializerOptions BuildJsonOptions()
    {
        var opts = new JsonSerializerOptions
        {
            PropertyNamingPolicy = null, // keys match property names exactly (JsonPropertyName handles mapping)
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        };
        // Register every generated per-type JsonConverter (subclasses of
        // JsonConverter<T>) found in the generated model assembly.
        var modelAssembly = typeof(RequestStatusResponse).Assembly;
        foreach (var type in modelAssembly.GetTypes())
        {
            if (type.IsAbstract || type.ContainsGenericParameters) continue;
            for (var baseType = type.BaseType; baseType != null && baseType != typeof(object); baseType = baseType.BaseType)
            {
                if (baseType.IsGenericType && baseType.GetGenericTypeDefinition() == typeof(JsonConverter<>))
                {
                    var ctor = type.GetConstructor(Type.EmptyTypes);
                    if (ctor != null)
                        opts.Converters.Add((JsonConverter)ctor.Invoke(null));
                    break;
                }
            }
        }
        return opts;
    }

    private OrbClient(HttpClient http, string baseUrl, Scheduler scheduler, SubprocessManager? proc)
    {
        _http = http;
        _baseUrl = baseUrl;
        _scheduler = scheduler;
        _proc = proc;
    }

    // ---------------------------------------------------------------------------
    // Factory
    // ---------------------------------------------------------------------------

    /// <summary>Create and initialize an OrbClient.</summary>
    public static async Task<OrbClient> CreateAsync(ClientConfig? config = null, CancellationToken ct = default)
    {
        config ??= new ClientConfig();
        var auth = config.Auth;
        var scheduler = config.Scheduler;

        var socketPath = config.SocketPath ?? "";
        SubprocessManager? proc = null;

        if (config.Process != null)
        {
            var procCfg = config.Process;
            if (string.IsNullOrEmpty(socketPath))
            {
                socketPath = procCfg.SocketPath ?? TempSocketPath();
                // If the config didn't have SocketPath, re-create with it
                procCfg = new ProcessConfig
                {
                    Binary = procCfg.Binary,
                    ExtraArgs = procCfg.ExtraArgs,
                    Env = procCfg.Env,
                    SocketPath = socketPath,
                    Port = procCfg.Port,
                    StartTimeoutMs = procCfg.StartTimeoutMs,
                    StopTimeoutMs = procCfg.StopTimeoutMs,
                    PythonPath = procCfg.PythonPath,
                };
            }
            proc = new SubprocessManager(procCfg);
            await proc.StartAsync(ct).ConfigureAwait(false);
        }

        var baseUrl = !string.IsNullOrEmpty(socketPath)
            ? "http://localhost"
            : (config.BaseUrl ?? "http://localhost:8000");

        // Build handler chain (inner → UDS/TCP → retry → auth)
        HttpMessageHandler inner = string.IsNullOrEmpty(socketPath)
            ? new SocketsHttpHandler { PooledConnectionIdleTimeout = TimeSpan.FromMinutes(5) }
            : UdsHttpHandlerFactory.Create(socketPath);

        var retry = new RetryDelegatingHandler(config.Retry ?? new RetryConfig(), inner);
        var authHandler = new AuthDelegatingHandler(auth, retry);

        var http = new HttpClient(authHandler)
        {
            BaseAddress = new Uri(baseUrl),
            Timeout = TimeSpan.FromMilliseconds(config.TimeoutMs),
        };

        return new OrbClient(http, baseUrl, scheduler, proc);
    }

    /// <summary>Stop the managed subprocess (if any) and release resources.</summary>
    public async ValueTask DisposeAsync()
    {
        _http.Dispose();
        if (_proc != null)
        {
            await _proc.DisposeAsync().ConfigureAwait(false);
            _proc = null;
        }
    }

    /// <summary>Returns true if the managed process (if any) is currently healthy.</summary>
    public bool Healthy => _proc?.Healthy ?? true;

    // ---------------------------------------------------------------------------
    // HTTP helpers
    // ---------------------------------------------------------------------------

    private void CheckHealth()
    {
        if (_proc != null && !_proc.Healthy)
            throw new OrbUnavailableException("managed ORB process is unhealthy");
    }

    private void AddSchedulerHeader(HttpRequestMessage req)
    {
        if (_scheduler != Scheduler.Default)
            req.Headers.TryAddWithoutValidation("X-ORB-Scheduler", _scheduler.WireValue());
    }

    private async Task<T> GetAsync<T>(string path, Dictionary<string, string>? query = null, CancellationToken ct = default)
    {
        CheckHealth();
        var url = BuildUrl(path, query);
        using var req = new HttpRequestMessage(HttpMethod.Get, url);
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        AddSchedulerHeader(req);
        return await SendAndDeserializeAsync<T>(req, ct).ConfigureAwait(false);
    }

    private async Task<T> PostAsync<T>(string path, object? body = null, CancellationToken ct = default)
    {
        CheckHealth();
        using var req = new HttpRequestMessage(HttpMethod.Post, path);
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        AddSchedulerHeader(req);
        if (body != null)
            req.Content = new StringContent(JsonSerializer.Serialize(body, JsonOpts), Encoding.UTF8, "application/json");
        return await SendAndDeserializeAsync<T>(req, ct).ConfigureAwait(false);
    }

    private async Task<T> PutAsync<T>(string path, object? body = null, CancellationToken ct = default)
    {
        CheckHealth();
        using var req = new HttpRequestMessage(HttpMethod.Put, path);
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        AddSchedulerHeader(req);
        if (body != null)
            req.Content = new StringContent(JsonSerializer.Serialize(body, JsonOpts), Encoding.UTF8, "application/json");
        return await SendAndDeserializeAsync<T>(req, ct).ConfigureAwait(false);
    }

    private async Task<T> DeleteAsync<T>(string path, Dictionary<string, string>? query = null, CancellationToken ct = default)
    {
        CheckHealth();
        var url = BuildUrl(path, query);
        using var req = new HttpRequestMessage(HttpMethod.Delete, url);
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        AddSchedulerHeader(req);
        return await SendAndDeserializeAsync<T>(req, ct).ConfigureAwait(false);
    }

    private async Task<T> SendAndDeserializeAsync<T>(HttpRequestMessage req, CancellationToken ct)
    {
        var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseContentRead, ct)
                              .ConfigureAwait(false);

        if (!resp.IsSuccessStatusCode)
        {
            var body = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            var code = ExtractErrorCode(body);
            var msg = ExtractErrorMessage(body) ?? resp.ReasonPhrase ?? resp.StatusCode.ToString();
            var requestId = ExtractRequestId(resp);

            // Construct the most specific typed sentinel for the status
            // (401/403/404/409/503/408) so callers can catch it directly, else
            // fall back to the base OrbApiException.
            throw OrbApiException.ForStatus((int)resp.StatusCode, msg, code, body, requestId);
        }

        var json = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);

        if (typeof(T) == typeof(string)) return (T)(object)json;

        if (string.IsNullOrWhiteSpace(json)) return default!;

        return JsonSerializer.Deserialize<T>(json, JsonOpts) ?? default!;
    }

    private static string BuildUrl(string path, Dictionary<string, string>? query)
    {
        if (query == null || query.Count == 0) return path;
        var qs = string.Join("&", query
            .Where(kv => kv.Value != null)
            .Select(kv => $"{Uri.EscapeDataString(kv.Key)}={Uri.EscapeDataString(kv.Value)}"));
        return $"{path}?{qs}";
    }

    // Server-assigned request ID (X-Request-ID / X-Correlation-ID) for support
    // correlation.  Returned on the typed error so callers can quote it.
    private static string? ExtractRequestId(HttpResponseMessage resp)
    {
        foreach (var header in new[] { "X-Request-ID", "X-Correlation-ID" })
        {
            if (resp.Headers.TryGetValues(header, out var values))
            {
                var v = values.FirstOrDefault();
                if (!string.IsNullOrEmpty(v)) return v;
            }
        }
        return null;
    }

    private static string? ExtractErrorCode(string body)
    {
        try
        {
            var doc = JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("code", out var code)) return code.GetString();
            if (doc.RootElement.TryGetProperty("error", out var error)) return error.GetString();
        }
        catch { }
        return null;
    }

    private static string? ExtractErrorMessage(string body)
    {
        try
        {
            var doc = JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("message", out var msg)) return msg.GetString();
            if (doc.RootElement.TryGetProperty("detail", out var detail))
            {
                if (detail.ValueKind == JsonValueKind.String) return detail.GetString();
                return detail.GetRawText();
            }
        }
        catch { }
        return null;
    }

    // ---------------------------------------------------------------------------
    // System / Observability — 4 operations
    // ---------------------------------------------------------------------------

    /// <summary>health_check_health_get — GET /health</summary>
    public async Task<Dictionary<string, object?>> HealthAsync(CancellationToken ct = default)
    {
        CheckHealth();
        using var req = new HttpRequestMessage(HttpMethod.Get, "/health");
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        AddSchedulerHeader(req);
        // Accept 200 (healthy) and 503 (degraded — ORB returns 503 for degraded health)
        var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseContentRead, ct).ConfigureAwait(false);
        if (resp.StatusCode != System.Net.HttpStatusCode.OK &&
            resp.StatusCode != System.Net.HttpStatusCode.ServiceUnavailable)
        {
            var errBody = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            throw OrbApiException.ForStatus((int)resp.StatusCode,
                ExtractErrorMessage(errBody) ?? resp.ReasonPhrase ?? "health check failed",
                ExtractErrorCode(errBody), errBody, ExtractRequestId(resp));
        }
        var json = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        return JsonSerializer.Deserialize<Dictionary<string, object?>>(json, JsonOpts) ?? [];
    }

    /// <summary>info_info_get — GET /info</summary>
    public async Task<Dictionary<string, object?>> InfoAsync(CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>("/info", ct: ct).ConfigureAwait(false);

    /// <summary>metrics_metrics_get — GET /metrics</summary>
    public async Task<string> MetricsAsync(CancellationToken ct = default)
    {
        CheckHealth();
        using var req = new HttpRequestMessage(HttpMethod.Get, "/metrics");
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/plain"));
        AddSchedulerHeader(req);
        var resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
        if (!resp.IsSuccessStatusCode)
            throw OrbApiException.ForStatus((int)resp.StatusCode,
                resp.ReasonPhrase ?? "metrics failed", requestId: ExtractRequestId(resp));
        return await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
    }

    /// <summary>get_dashboard_summary_api_v1_system_dashboard_get — GET /api/v1/system/dashboard</summary>
    public async Task<Dictionary<string, object?>> GetDashboardSummaryAsync(CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>("/api/v1/system/dashboard", ct: ct).ConfigureAwait(false);

    // ---------------------------------------------------------------------------
    // Templates — 8 operations
    // ---------------------------------------------------------------------------

    /// <summary>list_templates_api_v1_templates__get — GET /api/v1/templates/</summary>
    public async Task<TemplateListResponse> ListTemplatesAsync(CancellationToken ct = default)
        => await GetAsync<TemplateListResponse>("/api/v1/templates/", ct: ct).ConfigureAwait(false);

    /// <summary>get_template_api_v1_templates__template_id__get — GET /api/v1/templates/{template_id}</summary>
    public async Task<TemplateItem> GetTemplateAsync(string templateId, CancellationToken ct = default)
        => await GetAsync<TemplateItem>($"/api/v1/templates/{Uri.EscapeDataString(templateId)}", ct: ct)
                 .ConfigureAwait(false);

    /// <summary>create_template_api_v1_templates__post — POST /api/v1/templates/</summary>
    public async Task<TemplateMutationResponse> CreateTemplateAsync(TemplateCreateRequest body, CancellationToken ct = default)
        => await PostAsync<TemplateMutationResponse>("/api/v1/templates/", body, ct).ConfigureAwait(false);

    /// <summary>update_template_api_v1_templates__template_id__put — PUT /api/v1/templates/{template_id}</summary>
    public async Task<TemplateMutationResponse> UpdateTemplateAsync(string templateId, TemplateUpdateRequest body, CancellationToken ct = default)
        => await PutAsync<TemplateMutationResponse>($"/api/v1/templates/{Uri.EscapeDataString(templateId)}", body, ct)
                 .ConfigureAwait(false);

    /// <summary>delete_template_api_v1_templates__template_id__delete — DELETE /api/v1/templates/{template_id}</summary>
    public async Task<Dictionary<string, object?>> DeleteTemplateAsync(string templateId, CancellationToken ct = default)
        => await DeleteAsync<Dictionary<string, object?>>($"/api/v1/templates/{Uri.EscapeDataString(templateId)}", ct: ct)
                 .ConfigureAwait(false);

    /// <summary>validate_template_api_v1_templates_validate_post — POST /api/v1/templates/validate</summary>
    public async Task<Dictionary<string, object?>> ValidateTemplateAsync(object? body = null, CancellationToken ct = default)
        => await PostAsync<Dictionary<string, object?>>("/api/v1/templates/validate", body, ct).ConfigureAwait(false);

    /// <summary>refresh_templates_api_v1_templates_refresh_post — POST /api/v1/templates/refresh</summary>
    public async Task<TemplateListResponse> RefreshTemplatesAsync(CancellationToken ct = default)
        => await PostAsync<TemplateListResponse>("/api/v1/templates/refresh", ct: ct).ConfigureAwait(false);

    /// <summary>generate_templates_api_v1_templates_generate_post — POST /api/v1/templates/generate</summary>
    public async Task<TemplateListResponse> GenerateTemplatesAsync(GenerateTemplatesBody body, CancellationToken ct = default)
        => await PostAsync<TemplateListResponse>("/api/v1/templates/generate", body, ct).ConfigureAwait(false);

    // ---------------------------------------------------------------------------
    // Machines — 8 operations
    // ---------------------------------------------------------------------------

    /// <summary>list_machines_api_v1_machines__get — GET /api/v1/machines/</summary>
    public async Task<MachineListResponse> ListMachinesAsync(
        string? status = null, string? requestId = null,
        int? limit = null, int? offset = null,
        CancellationToken ct = default)
    {
        var q = new Dictionary<string, string>();
        if (status != null) q["status"] = status;
        if (requestId != null) q["request_id"] = requestId;
        if (limit.HasValue) q["limit"] = limit.Value.ToString();
        if (offset.HasValue) q["offset"] = offset.Value.ToString();
        return await GetAsync<MachineListResponse>("/api/v1/machines/", q, ct).ConfigureAwait(false);
    }

    /// <summary>get_machine_api_v1_machines__machine_id__get — GET /api/v1/machines/{machine_id}</summary>
    public async Task<MachineItem> GetMachineAsync(string machineId, CancellationToken ct = default)
        => await GetAsync<MachineItem>($"/api/v1/machines/{Uri.EscapeDataString(machineId)}", ct: ct)
                 .ConfigureAwait(false);

    /// <summary>request_machines_api_v1_machines_request_post — POST /api/v1/machines/request</summary>
    public async Task<RequestOperationResponse> RequestMachinesAsync(RequestMachinesRequest body, CancellationToken ct = default)
        => await PostAsync<RequestOperationResponse>("/api/v1/machines/request", body, ct).ConfigureAwait(false);

    /// <summary>return_machines_api_v1_machines_return_post — POST /api/v1/machines/return</summary>
    public async Task<RequestOperationResponse> ReturnMachinesAsync(ReturnMachinesRequest body, CancellationToken ct = default)
        => await PostAsync<RequestOperationResponse>("/api/v1/machines/return", body, ct).ConfigureAwait(false);

    /// <summary>sync_machine_status_api_v1_machines__machine_id__status_get — GET /api/v1/machines/{machine_id}/status</summary>
    public async Task<MachineListResponse> SyncMachineStatusAsync(string machineId, CancellationToken ct = default)
        => await GetAsync<MachineListResponse>($"/api/v1/machines/{Uri.EscapeDataString(machineId)}/status", ct: ct)
                 .ConfigureAwait(false);

    /// <summary>get_machine_metrics_api_v1_machines__machine_id__metrics_get — GET /api/v1/machines/{machine_id}/metrics</summary>
    public async Task<Dictionary<string, object?>> GetMachineMetricsAsync(string machineId, string? range = null, CancellationToken ct = default)
    {
        var q = range != null ? new Dictionary<string, string> { ["range"] = range } : null;
        return await GetAsync<Dictionary<string, object?>>($"/api/v1/machines/{Uri.EscapeDataString(machineId)}/metrics", q, ct)
                     .ConfigureAwait(false);
    }

    /// <summary>purge_machine_api_v1_machines__machine_id__delete — DELETE /api/v1/machines/{machine_id}</summary>
    public async Task<Dictionary<string, object?>> PurgeMachineAsync(string machineId, CancellationToken ct = default)
        => await DeleteAsync<Dictionary<string, object?>>($"/api/v1/machines/{Uri.EscapeDataString(machineId)}", ct: ct)
                 .ConfigureAwait(false);

    // ---------------------------------------------------------------------------
    // Requests — 10 operations
    // ---------------------------------------------------------------------------

    /// <summary>
    /// list_requests_api_v1_requests__get — GET /api/v1/requests/
    /// <para>
    /// Exposes the canonical list-filter set shared across all SDKs: status,
    /// limit, offset, sync, cursor, q, sort, providerName, providerType,
    /// templateId, requestType, filterExpressions.
    /// </para>
    /// </summary>
    public async Task<RequestStatusResponse> ListRequestsAsync(
        string? status = null, int? limit = null, int? offset = null,
        bool? sync = null, string? cursor = null, string? q = null, string? sort = null,
        string? providerName = null, string? providerType = null, string? templateId = null,
        string? requestType = null, IEnumerable<string>? filterExpressions = null,
        CancellationToken ct = default)
    {
        var qp = new Dictionary<string, string>();
        if (status != null) qp["status"] = status;
        if (limit.HasValue) qp["limit"] = limit.Value.ToString();
        if (offset.HasValue) qp["offset"] = offset.Value.ToString();
        if (sync.HasValue) qp["sync"] = sync.Value.ToString().ToLowerInvariant();
        if (cursor != null) qp["cursor"] = cursor;
        if (q != null) qp["q"] = q;
        if (sort != null) qp["sort"] = sort;
        if (providerName != null) qp["provider_name"] = providerName;
        if (providerType != null) qp["provider_type"] = providerType;
        if (templateId != null) qp["template_id"] = templateId;
        if (requestType != null) qp["request_type"] = requestType;
        if (filterExpressions != null) qp["filter_expressions"] = string.Join(",", filterExpressions);
        return await GetAsync<RequestStatusResponse>("/api/v1/requests/", qp, ct).ConfigureAwait(false);
    }

    /// <summary>
    /// list_return_requests_api_v1_requests_return_get — GET /api/v1/requests/return
    /// <para>
    /// Exposes the canonical return-list filter set shared across SDKs: limit,
    /// offset, cursor, q, sort, providerName, providerType, filterExpressions.
    /// </para>
    /// </summary>
    public async Task<RequestStatusResponse> ListReturnRequestsAsync(
        int? limit = null, int? offset = null, string? cursor = null, string? q = null,
        string? sort = null, string? providerName = null, string? providerType = null,
        IEnumerable<string>? filterExpressions = null,
        CancellationToken ct = default)
    {
        var qp = new Dictionary<string, string>();
        if (limit.HasValue) qp["limit"] = limit.Value.ToString();
        if (offset.HasValue) qp["offset"] = offset.Value.ToString();
        if (cursor != null) qp["cursor"] = cursor;
        if (q != null) qp["q"] = q;
        if (sort != null) qp["sort"] = sort;
        if (providerName != null) qp["provider_name"] = providerName;
        if (providerType != null) qp["provider_type"] = providerType;
        if (filterExpressions != null) qp["filter_expressions"] = string.Join(",", filterExpressions);
        return await GetAsync<RequestStatusResponse>("/api/v1/requests/return", qp, ct).ConfigureAwait(false);
    }

    /// <summary>get_request_status_api_v1_requests__request_id__status_get — GET /api/v1/requests/{request_id}/status</summary>
    public async Task<RequestStatusResponse> GetRequestStatusAsync(string requestId, bool? verbose = null, CancellationToken ct = default)
    {
        var q = verbose.HasValue ? new Dictionary<string, string> { ["verbose"] = verbose.Value.ToString().ToLower() } : null;
        return await GetAsync<RequestStatusResponse>($"/api/v1/requests/{Uri.EscapeDataString(requestId)}/status", q, ct)
                     .ConfigureAwait(false);
    }

    /// <summary>get_request_timeline_api_v1_requests__request_id__timeline_get — GET /api/v1/requests/{request_id}/timeline</summary>
    public async Task<Dictionary<string, object?>> GetRequestTimelineAsync(string requestId, CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>($"/api/v1/requests/{Uri.EscapeDataString(requestId)}/timeline", ct: ct)
                 .ConfigureAwait(false);

    /// <summary>batch_get_request_status_api_v1_requests_status_post — POST /api/v1/requests/status</summary>
    public async Task<RequestStatusResponse> BatchGetRequestStatusAsync(BatchRequestStatusBody body, CancellationToken ct = default)
        => await PostAsync<RequestStatusResponse>("/api/v1/requests/status", body, ct).ConfigureAwait(false);

    /// <summary>
    /// cancel_request_api_v1_requests__request_id__delete — DELETE /api/v1/requests/{request_id}
    /// <para>Optional <paramref name="reason"/> is sent as the <c>reason</c> query parameter.</para>
    /// </summary>
    public async Task<Dictionary<string, object?>> CancelRequestAsync(string requestId, string? reason = null, CancellationToken ct = default)
    {
        var q = reason != null ? new Dictionary<string, string> { ["reason"] = reason } : null;
        return await DeleteAsync<Dictionary<string, object?>>(
            $"/api/v1/requests/{Uri.EscapeDataString(requestId)}", q, ct).ConfigureAwait(false);
    }

    /// <summary>purge_request_api_v1_requests__request_id__purge_post — POST /api/v1/requests/{request_id}/purge</summary>
    public async Task<Dictionary<string, object?>> PurgeRequestAsync(string requestId, CancellationToken ct = default)
        => await PostAsync<Dictionary<string, object?>>($"/api/v1/requests/{Uri.EscapeDataString(requestId)}/purge", ct: ct)
                 .ConfigureAwait(false);

    /// <summary>
    /// stream_request_status_api_v1_requests__request_id__stream_get
    /// GET /api/v1/requests/{request_id}/stream
    ///
    /// Returns an IAsyncEnumerable that yields StreamEvent objects.
    /// Reconnects with back-off if the connection is dropped.
    /// Auth headers are applied on each (re)connection.
    /// </summary>
    public async IAsyncEnumerable<StreamEvent> StreamRequestStatusAsync(
        string requestId,
        int intervalSeconds = 2,
        int timeoutSeconds = 300,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        CheckHealth();

        async Task<Stream> Connect(string? lastEventId, CancellationToken innerCt)
        {
            var path = $"/api/v1/requests/{Uri.EscapeDataString(requestId)}/stream" +
                       $"?interval={intervalSeconds}&timeout={timeoutSeconds}";
            using var req = new HttpRequestMessage(HttpMethod.Get, path);
            req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
            AddSchedulerHeader(req);
            if (lastEventId != null)
                req.Headers.TryAddWithoutValidation("Last-Event-ID", lastEventId);

            var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, innerCt)
                                  .ConfigureAwait(false);
            if (!resp.IsSuccessStatusCode)
            {
                var body = await resp.Content.ReadAsStringAsync(innerCt).ConfigureAwait(false);
                throw OrbApiException.ForStatus((int)resp.StatusCode,
                    ExtractErrorMessage(body) ?? $"SSE HTTP {resp.StatusCode}",
                    ExtractErrorCode(body), body, ExtractRequestId(resp));
            }
            return await resp.Content.ReadAsStreamAsync(innerCt).ConfigureAwait(false);
        }

        await foreach (var frame in SseStream.StreamAsync(Connect, ct: ct).ConfigureAwait(false))
        {
            if (frame.Data.Trim() == "{}") yield break;

            var payload = SseStream.ParsePayload(frame);
            if (payload?.Requests == null) continue;

            foreach (var req2 in payload.Requests)
            {
                yield return new StreamEvent
                {
                    RequestId = req2.RequestId,
                    Status = req2.Status,
                    Message = req2.Message,
                    RequestedCount = req2.RequestedCount,
                    SuccessfulCount = req2.SuccessfulCount,
                    FailedCount = req2.FailedCount,
                    Machines = req2.Machines ?? [],
                };

                if (TerminalStatuses.All.Contains(req2.Status)) yield break;
            }
        }
    }

    /// <summary>
    /// Wait for a request to reach a terminal status.
    /// Returns the final StreamEvent.
    /// </summary>
    public async Task<StreamEvent> WaitForCompletionAsync(
        string requestId,
        int intervalSeconds = 2,
        int timeoutSeconds = 300,
        CancellationToken ct = default)
    {
        StreamEvent? last = null;
        await foreach (var ev in StreamRequestStatusAsync(requestId, intervalSeconds, timeoutSeconds, ct)
                                  .ConfigureAwait(false))
        {
            last = ev;
        }
        if (last == null)
            throw new OrbApiException(0, "stream ended without any events");
        return last;
    }

    /// <summary>
    /// stream_events_api_v1_events__get — GET /api/v1/events/
    ///
    /// Global SSE event bus. Returns IAsyncEnumerable of raw SseFrames.
    /// Auth headers are applied on each (re)connection.
    /// </summary>
    public async IAsyncEnumerable<SseFrame> StreamEventsAsync(
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        CheckHealth();

        async Task<Stream> Connect(string? lastEventId, CancellationToken innerCt)
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, "/api/v1/events/");
            req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
            AddSchedulerHeader(req);
            if (lastEventId != null)
                req.Headers.TryAddWithoutValidation("Last-Event-ID", lastEventId);

            var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, innerCt)
                                  .ConfigureAwait(false);
            if (!resp.IsSuccessStatusCode)
            {
                var body = await resp.Content.ReadAsStringAsync(innerCt).ConfigureAwait(false);
                throw OrbApiException.ForStatus((int)resp.StatusCode,
                    ExtractErrorMessage(body) ?? $"Event stream HTTP {resp.StatusCode}",
                    ExtractErrorCode(body), body, ExtractRequestId(resp));
            }

            return await resp.Content.ReadAsStreamAsync(innerCt).ConfigureAwait(false);
        }

        await foreach (var frame in SseStream.StreamAsync(Connect, ct: ct).ConfigureAwait(false))
            yield return frame;
    }

    // ---------------------------------------------------------------------------
    // Providers — 4 operations
    // ---------------------------------------------------------------------------

    /// <summary>list_providers_api_v1_providers__get — GET /api/v1/providers/</summary>
    public async Task<Dictionary<string, object?>> ListProvidersAsync(CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>("/api/v1/providers/", ct: ct).ConfigureAwait(false);

    /// <summary>get_all_provider_schemas_api_v1_providers_schemas_get — GET /api/v1/providers/schemas</summary>
    public async Task<Dictionary<string, object?>> GetAllProviderSchemasAsync(CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>("/api/v1/providers/schemas", ct: ct).ConfigureAwait(false);

    /// <summary>get_provider_schema_api_v1_providers__name__schema_get — GET /api/v1/providers/{name}/schema</summary>
    public async Task<Dictionary<string, object?>> GetProviderSchemaAsync(string name, CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>($"/api/v1/providers/{Uri.EscapeDataString(name)}/schema", ct: ct)
                 .ConfigureAwait(false);

    /// <summary>get_providers_health_api_v1_providers_health_get — GET /api/v1/providers/health</summary>
    public async Task<Dictionary<string, object?>> GetProvidersHealthAsync(CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>("/api/v1/providers/health", ct: ct).ConfigureAwait(false);

    // ---------------------------------------------------------------------------
    // Config — 7 operations
    // ---------------------------------------------------------------------------

    /// <summary>get_full_config_api_v1_config__get — GET /api/v1/config/</summary>
    public async Task<Dictionary<string, object?>> GetFullConfigAsync(string? source = null, CancellationToken ct = default)
    {
        var q = source != null ? new Dictionary<string, string> { ["source"] = source } : null;
        return await GetAsync<Dictionary<string, object?>>("/api/v1/config/", q, ct).ConfigureAwait(false);
    }

    /// <summary>get_config_sources_api_v1_config_sources_get — GET /api/v1/config/sources</summary>
    public async Task<Dictionary<string, object?>> GetConfigSourcesAsync(CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>("/api/v1/config/sources", ct: ct).ConfigureAwait(false);

    /// <summary>get_config_value_api_v1_config__key__get — GET /api/v1/config/{key}</summary>
    public async Task<Dictionary<string, object?>> GetConfigValueAsync(string key, CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>($"/api/v1/config/{Uri.EscapeDataString(key)}", ct: ct)
                 .ConfigureAwait(false);

    /// <summary>set_config_value_api_v1_config__key__put — PUT /api/v1/config/{key}</summary>
    public async Task<Dictionary<string, object?>> SetConfigValueAsync(string key, SetValueRequest body, CancellationToken ct = default)
        => await PutAsync<Dictionary<string, object?>>($"/api/v1/config/{Uri.EscapeDataString(key)}", body, ct)
                 .ConfigureAwait(false);

    /// <summary>save_config_api_v1_config_save_post — POST /api/v1/config/save</summary>
    public async Task<Dictionary<string, object?>> SaveConfigAsync(SaveRequest? body = null, CancellationToken ct = default)
        => await PostAsync<Dictionary<string, object?>>("/api/v1/config/save", body, ct).ConfigureAwait(false);

    /// <summary>validate_config_api_v1_config_validate_post — POST /api/v1/config/validate</summary>
    public async Task<Dictionary<string, object?>> ValidateConfigAsync(CancellationToken ct = default)
        => await PostAsync<Dictionary<string, object?>>("/api/v1/config/validate", ct: ct).ConfigureAwait(false);

    // ---------------------------------------------------------------------------
    // Admin — 4 operations
    // ---------------------------------------------------------------------------

    /// <summary>wipe_database_api_v1_admin_database_wipe_post — POST /api/v1/admin/database/wipe</summary>
    public async Task<Dictionary<string, object?>> WipeDatabaseAsync(bool confirm, CancellationToken ct = default)
        => await PostAsync<Dictionary<string, object?>>("/api/v1/admin/database/wipe", new { confirm }, ct)
                 .ConfigureAwait(false);

    /// <summary>init_orb_api_v1_admin_init_post — POST /api/v1/admin/init</summary>
    public async Task<Dictionary<string, object?>> InitOrbAsync(InitBody body, CancellationToken ct = default)
        => await PostAsync<Dictionary<string, object?>>("/api/v1/admin/init", body, ct).ConfigureAwait(false);

    /// <summary>cleanup_database_api_v1_admin_database_cleanup_post — POST /api/v1/admin/database/cleanup</summary>
    public async Task<Dictionary<string, object?>> CleanupDatabaseAsync(CleanupDatabaseBody body, CancellationToken ct = default)
        => await PostAsync<Dictionary<string, object?>>("/api/v1/admin/database/cleanup", body, ct).ConfigureAwait(false);

    /// <summary>reload_config_api_v1_admin_reload_config_post — POST /api/v1/admin/reload-config</summary>
    public async Task<Dictionary<string, object?>> ReloadConfigAsync(CancellationToken ct = default)
        => await PostAsync<Dictionary<string, object?>>("/api/v1/admin/reload-config", ct: ct).ConfigureAwait(false);

    // ---------------------------------------------------------------------------
    // Me / Observability — 2 operations
    // ---------------------------------------------------------------------------

    /// <summary>get_me_api_v1_me__get — GET /api/v1/me/</summary>
    public async Task<Dictionary<string, object?>> GetMeAsync(CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>("/api/v1/me/", ct: ct).ConfigureAwait(false);

    /// <summary>get_telemetry_status_api_v1_observability_telemetry_get — GET /api/v1/observability/telemetry</summary>
    public async Task<Dictionary<string, object?>> GetTelemetryStatusAsync(CancellationToken ct = default)
        => await GetAsync<Dictionary<string, object?>>("/api/v1/observability/telemetry", ct: ct).ConfigureAwait(false);

    // ---------------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------------

    /// <summary>Generate a temporary UNIX socket path for a managed ORB process.</summary>
    public static string TempSocketPath() =>
        Path.Combine(Path.GetTempPath(), $"orb-{System.Environment.ProcessId}-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}.sock");
}
