package org.finos.openresourcebroker.sdk.client;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

import org.finos.openresourcebroker.sdk.auth.AuthStrategy;
import org.finos.openresourcebroker.sdk.auth.AwsSigV4Auth;
import org.finos.openresourcebroker.sdk.model.*;
import org.finos.openresourcebroker.sdk.process.SubprocessManager;
import org.finos.openresourcebroker.sdk.process.ProcessConfig;
import org.finos.openresourcebroker.sdk.sse.OrbSsePayload;
import org.finos.openresourcebroker.sdk.sse.SseFrame;
import org.finos.openresourcebroker.sdk.sse.SseReader;
import org.finos.openresourcebroker.sdk.transport.RawHttpClient;

import java.io.*;
import java.time.Duration;
import java.util.*;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import java.util.logging.Logger;

/**
 * ORB Java SDK client.
 *
 * <p>Covers all 44 operations from {@code sdk/spec/openapi.json}.
 *
 * <p>Two operating modes:
 * <ul>
 *   <li><b>Spawn mode</b>: client starts ORB as a child process over a Unix domain socket.
 *       Use {@link Builder#process(ProcessConfig)} to configure.
 *   <li><b>Remote mode</b>: client connects to an existing ORB instance via TCP.
 *       An {@code https://} base URL is transported over TLS (default port 443,
 *       certificate + hostname verification); {@code http://} is plaintext
 *       (default port 80) for local development only.
 *       Use {@link Builder#baseUrl(String)} to configure.
 * </ul>
 *
 * <p>Example (spawn mode):
 * <pre>{@code
 * var proc = ProcessConfig.builder()
 *     .socketPath("/tmp/orb-test.sock")
 *     .startTimeout(Duration.ofSeconds(30))
 *     .build();
 *
 * try (var client = OrbClient.builder().process(proc).build()) {
 *     var templates = client.listTemplates();
 *     // ...
 * }
 * }</pre>
 *
 * <p>Example (remote mode):
 * <pre>{@code
 * var client = OrbClient.builder()
 *     .baseUrl("https://my-orb.example.com")
 *     .auth(new BearerTokenAuth("my-token"))
 *     .build();
 * }</pre>
 */
public class OrbClient implements Closeable {

    private static final Logger LOG = Logger.getLogger(OrbClient.class.getName());

    /** Terminal request statuses (stream stops reconnecting when these are reached). */
    private static final Set<String> TERMINAL_STATUSES = Set.of(
            "complete", "completed", "failed", "error", "cancelled", "canceled",
            "partial", "timeout");

    private final RawHttpClient http;
    private final ObjectMapper mapper;
    private final SubprocessManager processManager;
    private final Scheduler scheduler;

    private OrbClient(RawHttpClient http, ObjectMapper mapper,
                      SubprocessManager processManager, Scheduler scheduler) {
        this.http = http;
        this.mapper = mapper;
        this.processManager = processManager;
        this.scheduler = scheduler != null ? scheduler : Scheduler.DEFAULT;
    }

    /** Returns true if the managed process (if any) is healthy. */
    public boolean isHealthy() {
        return processManager == null || processManager.isHealthy();
    }

    /** Stop the managed subprocess (if any) and release resources. */
    @Override
    public void close() {
        if (processManager != null) {
            processManager.stop();
        }
    }

    // ======================================================================
    // System / Observability — 4 operations
    // ======================================================================

    /**
     * healthCheck — GET /health
     *
     * <p>Returns the parsed health body for ALL statuses, including {@code 503}:
     * a degraded/unhealthy response carries a valid body and is data, not an
     * error.  This call never throws on {@code 503} and is never retry-looped,
     * matching the Go/TypeScript/Kotlin/C# SDKs.  Other {@code >= 400} statuses
     * (e.g. {@code 401} when auth is required) still throw.
     */
    public Map<String, Object> health() throws Exception {
        checkHealth();
        RawHttpClient.HttpResult result = http.getNoRetry("/health", null);
        int status = result.statusCode();
        // 503 is a valid degraded/unhealthy health response, not an error.
        if (status >= 400 && status != 503) {
            throwApiException(status, result.body(), result.headers());
        }
        if (result.body() == null || result.body().isBlank()) {
            return null;
        }
        return mapper.readValue(result.body(), new TypeReference<>() {});
    }

    /** getMetrics — GET /metrics */
    public String metrics() throws Exception {
        checkHealth();
        RawHttpClient.HttpResult result = http.getText("/metrics");
        return result.body();
    }

    /** getServiceInfo — GET /info */
    public Map<String, Object> info() throws Exception {
        return get("/info", null, new TypeReference<>() {});
    }

    /** getDashboardSummary — GET /api/v1/system/dashboard */
    public Map<String, Object> getDashboardSummary() throws Exception {
        return get("/api/v1/system/dashboard", null, new TypeReference<>() {});
    }

    // ======================================================================
    // Templates — 8 operations
    // ======================================================================

    /**
     * listTemplates — GET /api/v1/templates/
     *
     * @param providerApi optional filter by provider API
     * @param limit       max results (default 50)
     * @param offset      skip this many results
     */
    /** listTemplates — GET /api/v1/templates/ (no filters). */
    public TemplateListResponse listTemplates() throws Exception {
        return listTemplates(null, null, null);
    }

    public TemplateListResponse listTemplates(String providerApi, Integer limit, Integer offset)
            throws Exception {
        Map<String, String> params = new LinkedHashMap<>();
        if (providerApi != null) params.put("provider_api", providerApi);
        if (limit != null) params.put("limit", String.valueOf(limit));
        if (offset != null) params.put("offset", String.valueOf(offset));
        return get("/api/v1/templates/", params.isEmpty() ? null : params,
                new TypeReference<>() {});
    }

    /**
     * createTemplate — POST /api/v1/templates/
     */
    public TemplateMutationResponse createTemplate(TemplateCreateRequest request) throws Exception {
        return post("/api/v1/templates/", request, new TypeReference<>() {});
    }

    /**
     * validateTemplate — POST /api/v1/templates/validate
     */
    public TemplateMutationResponse validateTemplate(Map<String, Object> templateData)
            throws Exception {
        return post("/api/v1/templates/validate", templateData, new TypeReference<>() {});
    }

    /**
     * refreshTemplates — POST /api/v1/templates/refresh
     */
    public TemplateListResponse refreshTemplates() throws Exception {
        return post("/api/v1/templates/refresh", null, new TypeReference<>() {});
    }

    /**
     * generateTemplates — POST /api/v1/templates/generate
     */
    public TemplateListResponse generateTemplates(GenerateTemplatesBody body) throws Exception {
        return post("/api/v1/templates/generate", body, new TypeReference<>() {});
    }

    /**
     * getTemplate — GET /api/v1/templates/{template_id}
     *
     * <p>Returns the single {@link TemplateItem} (unwrapped from the server's list
     * envelope) to match the other SDKs, or {@code null} if the response is empty.
     */
    public TemplateItem getTemplate(String templateId) throws Exception {
        TemplateListResponse resp = get("/api/v1/templates/" + encode(templateId), null,
                new TypeReference<>() {});
        return firstOrNull(resp != null ? resp.getTemplates() : null);
    }

    /**
     * updateTemplate — PUT /api/v1/templates/{template_id}
     */
    public TemplateMutationResponse updateTemplate(String templateId, TemplateUpdateRequest request)
            throws Exception {
        return put("/api/v1/templates/" + encode(templateId), request, new TypeReference<>() {});
    }

    /**
     * deleteTemplate — DELETE /api/v1/templates/{template_id}
     */
    public TemplateMutationResponse deleteTemplate(String templateId) throws Exception {
        return delete("/api/v1/templates/" + encode(templateId), new TypeReference<>() {});
    }

    // ======================================================================
    // Machines — 7 operations
    // ======================================================================

    /**
     * requestMachines — POST /api/v1/machines/request
     */
    public RequestOperationResponse requestMachines(RequestMachinesRequest request)
            throws Exception {
        return post("/api/v1/machines/request", request, new TypeReference<>() {});
    }

    /**
     * returnMachines — POST /api/v1/machines/return
     */
    public RequestOperationResponse returnMachines(ReturnMachinesRequest request) throws Exception {
        return post("/api/v1/machines/return", request, new TypeReference<>() {});
    }

    /**
     * listMachines — GET /api/v1/machines/
     *
     * @param status       optional filter by machine status
     * @param providerName optional filter by provider name
     * @param requestId    optional filter by request ID
     * @param limit        max results
     * @param offset       skip results
     */
    /** listMachines — GET /api/v1/machines/ (no filters). */
    public MachineListResponse listMachines() throws Exception {
        return listMachines(null, null, null, null, null);
    }

    public MachineListResponse listMachines(String status, String providerName, String requestId,
                                             Integer limit, Integer offset) throws Exception {
        Map<String, String> params = new LinkedHashMap<>();
        if (status != null) params.put("status", status);
        if (providerName != null) params.put("provider_name", providerName);
        if (requestId != null) params.put("request_id", requestId);
        if (limit != null) params.put("limit", String.valueOf(limit));
        if (offset != null) params.put("offset", String.valueOf(offset));
        return get("/api/v1/machines/", params.isEmpty() ? null : params,
                new TypeReference<>() {});
    }

    /**
     * getMachine — GET /api/v1/machines/{machine_id}
     *
     * <p>Returns the single {@link MachineItem} (unwrapped from the server's list
     * envelope) to match the other SDKs, or {@code null} if the response is empty.
     */
    public MachineItem getMachine(String machineId) throws Exception {
        MachineListResponse resp = get("/api/v1/machines/" + encode(machineId), null,
                new TypeReference<>() {});
        return firstOrNull(resp != null ? resp.getMachines() : null);
    }

    /**
     * syncMachineStatus
     * — GET /api/v1/machines/{machine_id}/status
     *
     * <p>Returns the single {@link MachineItem} (unwrapped from the server's list
     * envelope), or {@code null} if the response is empty.
     */
    public MachineItem syncMachineStatus(String machineId) throws Exception {
        MachineListResponse resp = get("/api/v1/machines/" + encode(machineId) + "/status", null,
                new TypeReference<>() {});
        return firstOrNull(resp != null ? resp.getMachines() : null);
    }

    /**
     * purgeMachine — DELETE /api/v1/machines/{machine_id}
     */
    public Map<String, Object> purgeMachine(String machineId) throws Exception {
        return delete("/api/v1/machines/" + encode(machineId), new TypeReference<>() {});
    }

    /**
     * getMachineMetrics
     * — GET /api/v1/machines/{machine_id}/metrics
     */
    public Map<String, Object> getMachineMetrics(String machineId) throws Exception {
        return get("/api/v1/machines/" + encode(machineId) + "/metrics", null,
                new TypeReference<>() {});
    }

    // ======================================================================
    // Requests — 7 operations
    // ======================================================================

    /** listRequests — GET /api/v1/requests/ (no filters). */
    public RequestStatusResponse listRequests() throws Exception {
        return listRequests(new ListRequestsParams());
    }

    /**
     * listRequests — GET /api/v1/requests/
     *
     * <p>Exposes the canonical filter set shared with the Go/TypeScript SDKs:
     * status, limit, offset, sync, cursor, q, sort, provider_name, provider_type,
     * template_id, request_type, filter_expressions.
     *
     * @param p filter/pagination parameters (may be {@code null} for none)
     */
    public RequestStatusResponse listRequests(ListRequestsParams p) throws Exception {
        Map<String, String> params = new LinkedHashMap<>();
        if (p != null) {
            if (p.getStatus() != null) params.put("status", p.getStatus());
            if (p.getLimit() != null) params.put("limit", String.valueOf(p.getLimit()));
            if (p.getOffset() != null) params.put("offset", String.valueOf(p.getOffset()));
            if (p.getSync() != null) params.put("sync", String.valueOf(p.getSync()));
            if (p.getCursor() != null) params.put("cursor", p.getCursor());
            if (p.getQ() != null) params.put("q", p.getQ());
            if (p.getSort() != null) params.put("sort", p.getSort());
            if (p.getProviderName() != null) params.put("provider_name", p.getProviderName());
            if (p.getProviderType() != null) params.put("provider_type", p.getProviderType());
            if (p.getTemplateId() != null) params.put("template_id", p.getTemplateId());
            if (p.getRequestType() != null) params.put("request_type", p.getRequestType());
            if (p.getFilterExpressions() != null && !p.getFilterExpressions().isEmpty()) {
                params.put("filter_expressions", String.join(",", p.getFilterExpressions()));
            }
        }
        return get("/api/v1/requests/", params.isEmpty() ? null : params,
                new TypeReference<>() {});
    }

    /** listReturnRequests — GET /api/v1/requests/return (no filters). */
    public RequestStatusResponse listReturnRequests() throws Exception {
        return listReturnRequests(new ListReturnRequestsParams());
    }

    /**
     * listReturnRequests — GET /api/v1/requests/return
     *
     * <p>Exposes the canonical filter set shared with the Go/TypeScript SDKs:
     * limit, offset, cursor, q, sort, provider_name, provider_type,
     * filter_expressions.
     *
     * @param p filter/pagination parameters (may be {@code null} for none)
     */
    public RequestStatusResponse listReturnRequests(ListReturnRequestsParams p) throws Exception {
        Map<String, String> params = new LinkedHashMap<>();
        if (p != null) {
            if (p.getLimit() != null) params.put("limit", String.valueOf(p.getLimit()));
            if (p.getOffset() != null) params.put("offset", String.valueOf(p.getOffset()));
            if (p.getCursor() != null) params.put("cursor", p.getCursor());
            if (p.getQ() != null) params.put("q", p.getQ());
            if (p.getSort() != null) params.put("sort", p.getSort());
            if (p.getProviderName() != null) params.put("provider_name", p.getProviderName());
            if (p.getProviderType() != null) params.put("provider_type", p.getProviderType());
            if (p.getFilterExpressions() != null && !p.getFilterExpressions().isEmpty()) {
                params.put("filter_expressions", String.join(",", p.getFilterExpressions()));
            }
        }
        return get("/api/v1/requests/return", params.isEmpty() ? null : params,
                new TypeReference<>() {});
    }

    /**
     * getRequestStatus
     * — GET /api/v1/requests/{request_id}/status
     */
    public RequestStatusResponse getRequestStatus(String requestId, Boolean verbose)
            throws Exception {
        Map<String, String> params = new LinkedHashMap<>();
        if (verbose != null) params.put("verbose", String.valueOf(verbose));
        return get("/api/v1/requests/" + encode(requestId) + "/status",
                params.isEmpty() ? null : params, new TypeReference<>() {});
    }

    /**
     * getRequest
     * — GET /api/v1/requests/{request_id}
     */
    public RequestStatusResponse getRequest(String requestId, Boolean verbose)
            throws Exception {
        Map<String, String> params = new LinkedHashMap<>();
        if (verbose != null) params.put("verbose", String.valueOf(verbose));
        return get("/api/v1/requests/" + encode(requestId),
                params.isEmpty() ? null : params, new TypeReference<>() {});
    }

    /**
     * batchGetRequestStatus — POST /api/v1/requests/status
     */
    public RequestStatusResponse batchGetRequestStatus(BatchRequestStatusBody body)
            throws Exception {
        return post("/api/v1/requests/status", body, new TypeReference<>() {});
    }

    /**
     * cancelRequest — DELETE /api/v1/requests/{request_id}
     *
     * @param reason optional cancellation reason
     */
    public RequestOperationResponse cancelRequest(String requestId, String reason)
            throws Exception {
        String path = "/api/v1/requests/" + encode(requestId);
        if (reason != null) {
            path += "?reason=" + encode(reason);
        }
        return delete(path, new TypeReference<>() {});
    }

    /**
     * purgeRequest
     * — POST /api/v1/requests/{request_id}/purge
     */
    public Map<String, Object> purgeRequest(String requestId) throws Exception {
        return post("/api/v1/requests/" + encode(requestId) + "/purge", null,
                new TypeReference<>() {});
    }

    /**
     * getRequestTimeline
     * — GET /api/v1/requests/{request_id}/timeline
     */
    public Map<String, Object> getRequestTimeline(String requestId) throws Exception {
        return get("/api/v1/requests/" + encode(requestId) + "/timeline", null,
                new TypeReference<>() {});
    }

    // ======================================================================
    // SSE streaming — 2 operations
    // ======================================================================

    /**
     * streamRequest
     * — GET /api/v1/requests/{request_id}/stream (SSE)
     *
     * <p>Opens an SSE stream and calls {@code eventConsumer} for each event until a
     * terminal status is received, the sentinel frame is seen, the deadline is
     * reached, or {@code keepGoing} returns {@code false}.
     *
     * <p>Reconnect semantics match the Go/TypeScript/Kotlin/C# SDKs:
     * <ul>
     *   <li>A connect status of {@code 4xx} is <b>terminal</b>: the typed
     *       {@link OrbApiException} propagates and the stream is not retried
     *       (the request is bad/absent/unauthorized — reconnecting cannot help).
     *   <li>A {@code 5xx} connect status or a mid-stream connection drop triggers
     *       reconnect with exponential back-off until the deadline.
     * </ul>
     *
     * @param requestId     the request to stream
     * @param intervalSecs  server poll interval (default 2.0, range 0.5–60)
     * @param timeoutSecs   max stream duration in seconds (default 300, range 1–3600)
     * @param eventConsumer callback invoked for each event
     * @param keepGoing     polled between events; return {@code false} to stop the
     *                      stream early.  May be {@code null} to run to completion.
     * @throws OrbApiException on a terminal 4xx connect status
     */
    public void streamRequestStatus(String requestId, Double intervalSecs, Double timeoutSecs,
                                     Consumer<StreamEvent> eventConsumer,
                                     BooleanSupplier keepGoing) throws Exception {
        checkHealth();
        double interval = intervalSecs != null ? intervalSecs : 2.0;
        double timeout  = timeoutSecs  != null ? timeoutSecs  : 300.0;

        String path = String.format("/api/v1/requests/%s/stream?interval=%.1f&timeout=%.1f",
                encode(requestId), interval, timeout);

        Duration backoff = Duration.ofSeconds(1);
        Duration maxBackoff = Duration.ofSeconds(30);
        long deadline = System.currentTimeMillis() + (long)(timeout * 1000);

        while (System.currentTimeMillis() < deadline) {
            if (keepGoing != null && !keepGoing.getAsBoolean()) return;
            // Connect: a 4xx connect status throws a typed OrbApiException that we
            // deliberately DO NOT catch, so it propagates as terminal. Only 5xx and
            // mid-stream IOExceptions are retried below.
            try (InputStream sseStream = openSseStreamOr5xxRetry(path);
                 SseReader reader = new SseReader(sseStream)) {

                if (sseStream == null) {
                    // 5xx connect — fall through to backoff/reconnect
                } else {
                    boolean terminal = false;
                    SseFrame frame;
                    while ((frame = reader.next()) != null) {
                        if (keepGoing != null && !keepGoing.getAsBoolean()) return;
                        if (frame.isSentinel()) {
                            terminal = true;
                            break;
                        }
                        OrbSsePayload payload = parseOrbSsePayload(frame.data());
                        if (payload != null) {
                            OrbSsePayload.OrbSseRequest req = payload.firstRequest();
                            if (req != null) {
                                StreamEvent event = toStreamEvent(req);
                                eventConsumer.accept(event);
                                String status = req.getStatus();
                                if (status != null && TERMINAL_STATUSES.contains(status)) {
                                    terminal = true;
                                    break;
                                }
                            }
                            backoff = Duration.ofSeconds(1); // reset after successful event
                        }
                    }
                    if (terminal) return;
                }
            } catch (IOException e) {
                // mid-stream connection dropped — reconnect
            }

            // Reconnect with backoff
            Thread.sleep(backoff.toMillis());
            backoff = backoff.multipliedBy(2);
            if (backoff.compareTo(maxBackoff) > 0) backoff = maxBackoff;
        }
    }

    /**
     * stream_request_status ... convenience overload that runs to completion
     * (terminal status, sentinel, or timeout) with no early-stop predicate.
     */
    public void streamRequestStatus(String requestId, Double intervalSecs, Double timeoutSecs,
                                     Consumer<StreamEvent> eventConsumer) throws Exception {
        streamRequestStatus(requestId, intervalSecs, timeoutSecs, eventConsumer, null);
    }

    /**
     * Submit-then-await helper: streams {@code requestId} to completion and returns
     * the final {@link StreamEvent} (the last event before a terminal status,
     * sentinel, or timeout).  Mirrors {@code waitForCompletion} in the
     * Go/TypeScript/Kotlin/C# SDKs.
     *
     * @return the final event, or {@code null} if the stream ended without events
     */
    public StreamEvent waitForCompletion(String requestId, Double intervalSecs, Double timeoutSecs)
            throws Exception {
        StreamEvent[] last = {null};
        streamRequestStatus(requestId, intervalSecs, timeoutSecs, e -> last[0] = e, null);
        return last[0];
    }

    /**
     * Open an SSE stream, translating a {@code 5xx} connect status into a
     * {@code null} return (signalling the caller to reconnect) while letting a
     * {@code 4xx} connect status propagate as a terminal {@link OrbApiException}.
     */
    private InputStream openSseStreamOr5xxRetry(String path) throws IOException {
        try {
            return http.openSseStream(path);
        } catch (OrbApiException e) {
            if (e.getStatusCode() >= 500) {
                return null; // reconnect
            }
            throw e; // 4xx — terminal
        }
    }

    /**
     * streamEvents — GET /api/v1/events/ (SSE global event bus)
     *
     * <p>Streams global ORB events, delivering a structured {@link SseFrame} to
     * {@code frameConsumer} for each event (matching the TypeScript/Kotlin/C#
     * SDKs which yield an SseFrame rather than a raw string).
     *
     * <p>A {@code 4xx} connect status is terminal (typed {@link OrbApiException}
     * propagates); {@code 5xx} / drops reconnect with back-off until the deadline.
     *
     * @param frameConsumer callback for each SSE frame
     * @param timeoutMs     max duration before returning (0 = read until EOF)
     * @throws OrbApiException on a terminal 4xx connect status
     */
    public void streamEvents(Consumer<SseFrame> frameConsumer, long timeoutMs) throws Exception {
        checkHealth();
        String path = "/api/v1/events/";
        long deadline = timeoutMs > 0 ? System.currentTimeMillis() + timeoutMs : Long.MAX_VALUE;

        Duration backoff = Duration.ofSeconds(1);
        Duration maxBackoff = Duration.ofSeconds(30);

        while (System.currentTimeMillis() < deadline) {
            try (InputStream sseStream = openSseStreamOr5xxRetry(path);
                 SseReader reader = new SseReader(sseStream)) {

                if (sseStream != null) {
                    SseFrame frame;
                    while ((frame = reader.next()) != null) {
                        if (frame.isSentinel()) return;
                        frameConsumer.accept(frame);
                        backoff = Duration.ofSeconds(1);
                        if (System.currentTimeMillis() >= deadline) return;
                    }
                }
            } catch (IOException e) {
                // mid-stream drop — reconnect
            }
            if (System.currentTimeMillis() >= deadline) return;
            Thread.sleep(Math.min(backoff.toMillis(), deadline - System.currentTimeMillis()));
            backoff = backoff.multipliedBy(2);
            if (backoff.compareTo(maxBackoff) > 0) backoff = maxBackoff;
        }
    }

    // ======================================================================
    // Providers — 4 operations
    // ======================================================================

    /** listProviders — GET /api/v1/providers/ */
    public Map<String, Object> listProviders() throws Exception {
        return get("/api/v1/providers/", null, new TypeReference<>() {});
    }

    /** getAllProviderSchemas — GET /api/v1/providers/schemas */
    public Map<String, Object> getAllProviderSchemas() throws Exception {
        return get("/api/v1/providers/schemas", null, new TypeReference<>() {});
    }

    /** getProviderSchema — GET /api/v1/providers/{name}/schema */
    public Map<String, Object> getProviderSchema(String name) throws Exception {
        return get("/api/v1/providers/" + encode(name) + "/schema", null,
                new TypeReference<>() {});
    }

    /** getProvidersHealth — GET /api/v1/providers/health */
    public Map<String, Object> getProvidersHealth() throws Exception {
        return get("/api/v1/providers/health", null, new TypeReference<>() {});
    }

    // ======================================================================
    // Observability — 1 operation
    // ======================================================================

    /** getTelemetryStatus — GET /api/v1/observability/telemetry */
    public Map<String, Object> getTelemetryStatus() throws Exception {
        return get("/api/v1/observability/telemetry", null, new TypeReference<>() {});
    }

    // ======================================================================
    // Me — 1 operation
    // ======================================================================

    /** getCurrentUser — GET /api/v1/me/ */
    public Map<String, Object> getMe() throws Exception {
        return get("/api/v1/me/", null, new TypeReference<>() {});
    }

    // ======================================================================
    // Admin — 4 operations
    // ======================================================================

    /** wipeDatabase — POST /api/v1/admin/database/wipe */
    public Map<String, Object> wipeDatabase(Map<String, Object> body) throws Exception {
        return post("/api/v1/admin/database/wipe", body, new TypeReference<>() {});
    }

    /** initOrb — POST /api/v1/admin/init */
    public Map<String, Object> initOrb(InitBody body) throws Exception {
        return post("/api/v1/admin/init", body, new TypeReference<>() {});
    }

    /** cleanupDatabase — POST /api/v1/admin/database/cleanup */
    public Map<String, Object> cleanupDatabase(CleanupDatabaseBody body) throws Exception {
        return post("/api/v1/admin/database/cleanup", body, new TypeReference<>() {});
    }

    /** reloadConfig — POST /api/v1/admin/reload-config */
    public Map<String, Object> reloadConfig() throws Exception {
        return post("/api/v1/admin/reload-config", null, new TypeReference<>() {});
    }

    // ======================================================================
    // Config — 5 operations
    // ======================================================================

    /** getFullConfig — GET /api/v1/config/ */
    public Map<String, Object> getFullConfig() throws Exception {
        return get("/api/v1/config/", null, new TypeReference<>() {});
    }

    /** getConfigSources — GET /api/v1/config/sources */
    public Map<String, Object> getConfigSources() throws Exception {
        return get("/api/v1/config/sources", null, new TypeReference<>() {});
    }

    /** saveConfig — POST /api/v1/config/save */
    public Map<String, Object> saveConfig(SaveRequest body) throws Exception {
        return post("/api/v1/config/save", body, new TypeReference<>() {});
    }

    /** validateConfig — POST /api/v1/config/validate */
    public Map<String, Object> validateConfig(Map<String, Object> body) throws Exception {
        return post("/api/v1/config/validate", body, new TypeReference<>() {});
    }

    /** getConfigValue — GET /api/v1/config/{key} */
    public Map<String, Object> getConfigValue(String key) throws Exception {
        return get("/api/v1/config/" + encode(key), null, new TypeReference<>() {});
    }

    /** setConfigValue — PUT /api/v1/config/{key} */
    public Map<String, Object> setConfigValue(String key, SetValueRequest body) throws Exception {
        return put("/api/v1/config/" + encode(key), body, new TypeReference<>() {});
    }

    // ======================================================================
    // Private HTTP helpers
    // ======================================================================

    private void checkHealth() {
        if (processManager != null && !processManager.isHealthy()) {
            throw new OrbUnavailableException("managed ORB process is unhealthy");
        }
    }

    private Map<String, String> schedulerHeaders() {
        if (scheduler != Scheduler.DEFAULT) {
            Map<String, String> h = new LinkedHashMap<>();
            h.put("X-ORB-Scheduler", scheduler.wireValue());
            return h;
        }
        return Collections.emptyMap();
    }

    /** Return the first element of a list, or {@code null} if null/empty. */
    private static <T> T firstOrNull(List<T> list) {
        return (list != null && !list.isEmpty()) ? list.get(0) : null;
    }

    private <T> T get(String path, Map<String, String> params, TypeReference<T> type)
            throws Exception {
        checkHealth();
        RawHttpClient.HttpResult result = http.get(path, params);
        return parseResult(result, type);
    }

    private <T> T post(String path, Object body, TypeReference<T> type) throws Exception {
        checkHealth();
        String jsonBody = body != null ? mapper.writeValueAsString(body) : "{}";
        RawHttpClient.HttpResult result = http.post(path, jsonBody);
        return parseResult(result, type);
    }

    private <T> T put(String path, Object body, TypeReference<T> type) throws Exception {
        checkHealth();
        String jsonBody = body != null ? mapper.writeValueAsString(body) : "{}";
        RawHttpClient.HttpResult result = http.put(path, jsonBody);
        return parseResult(result, type);
    }

    private <T> T delete(String path, TypeReference<T> type) throws Exception {
        checkHealth();
        RawHttpClient.HttpResult result = http.delete(path);
        return parseResult(result, type);
    }

    private <T> T parseResult(RawHttpClient.HttpResult result, TypeReference<T> type)
            throws IOException {
        int status = result.statusCode();
        if (status >= 400) {
            throwApiException(status, result.body(), result.headers());
        }
        if (result.body() == null || result.body().isBlank()) {
            return null;
        }
        return mapper.readValue(result.body(), type);
    }

    /** Extract the server-assigned request ID from response headers (lower-cased). */
    private static String requestIdFromHeaders(Map<String, String> headers) {
        if (headers == null) return null;
        for (String h : new String[]{"x-request-id", "x-correlation-id"}) {
            String v = headers.get(h);
            if (v != null && !v.isEmpty()) return v;
        }
        return null;
    }

    /**
     * Throw the most specific typed {@link OrbApiException} for the status,
     * carrying the machine-readable error code and the server request ID.
     */
    private void throwApiException(int status, String body, Map<String, String> headers)
            throws IOException {
        String requestId = requestIdFromHeaders(headers);
        if (body == null || body.isBlank()) {
            throw OrbApiException.forStatus(status, null, httpStatusText(status), requestId);
        }
        try {
            JsonNode node = mapper.readTree(body);
            // ORB error format: {"detail": "..."} or {"error": {"code": "...", "message": "..."}}
            if (node.has("error")) {
                JsonNode err = node.get("error");
                String code = err.has("code") ? err.get("code").asText() : null;
                String msg  = err.has("message") ? err.get("message").asText() : body;
                throw OrbApiException.forStatus(status, code, msg, requestId);
            }
            if (node.has("detail")) {
                String detail = node.get("detail").isTextual()
                        ? node.get("detail").asText()
                        : node.get("detail").toString();
                throw OrbApiException.forStatus(status, null, detail, requestId);
            }
        } catch (OrbApiException e) {
            throw e;
        } catch (Exception ignored) {}
        throw OrbApiException.forStatus(status, null, body, requestId);
    }

    private static String httpStatusText(int code) {
        return switch (code) {
            case 400 -> "Bad Request";
            case 401 -> "Unauthorized";
            case 403 -> "Forbidden";
            case 404 -> "Not Found";
            case 405 -> "Method Not Allowed";
            case 409 -> "Conflict";
            case 422 -> "Unprocessable Entity";
            case 429 -> "Too Many Requests";
            case 500 -> "Internal Server Error";
            case 502 -> "Bad Gateway";
            case 503 -> "Service Unavailable";
            default  -> "HTTP " + code;
        };
    }

    // ======================================================================
    // SSE helpers
    // ======================================================================

    private OrbSsePayload parseOrbSsePayload(String data) {
        if (data == null || data.isBlank()) return null;
        try {
            return mapper.readValue(data, OrbSsePayload.class);
        } catch (Exception e) {
            return null;
        }
    }

    private StreamEvent toStreamEvent(OrbSsePayload.OrbSseRequest req) {
        List<StreamEvent.MachineInfo> machines = new ArrayList<>();
        if (req.getMachines() != null) {
            for (OrbSsePayload.OrbSseMachine m : req.getMachines()) {
                machines.add(new StreamEvent.MachineInfo(
                        m.getMachineId(), m.getName(), m.getStatus(), m.getResult(),
                        m.getPrivateIp(), m.getPublicIp(), m.getLaunchTime(), m.getMessage()));
            }
        }
        return new StreamEvent(req.getRequestId(), req.getStatus(), req.getMessage(),
                req.getRequestedCount(), req.getSuccessfulCount(), req.getFailedCount(),
                machines);
    }

    // ======================================================================
    // Misc
    // ======================================================================

    private static String encode(String s) {
        if (s == null) return "";
        try {
            return java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20");
        } catch (UnsupportedEncodingException e) {
            throw new RuntimeException(e);
        }
    }

    // ======================================================================
    // Builder
    // ======================================================================

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private String baseUrl = "http://localhost:8000";
        private String socketPath;
        private AuthStrategy auth = AuthStrategy.NONE;
        private Duration timeout = Duration.ofSeconds(30);
        private int maxRetries = 3;
        private Duration retryBaseDelay = Duration.ofMillis(500);
        private ProcessConfig process;
        private Scheduler scheduler = Scheduler.DEFAULT;

        public Builder baseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
            return this;
        }

        public Builder socketPath(String socketPath) {
            this.socketPath = socketPath;
            return this;
        }

        public Builder auth(AuthStrategy auth) {
            this.auth = auth;
            return this;
        }

        public Builder timeout(Duration timeout) {
            this.timeout = timeout;
            return this;
        }

        public Builder maxRetries(int maxRetries) {
            this.maxRetries = maxRetries;
            return this;
        }

        public Builder retryBaseDelay(Duration delay) {
            this.retryBaseDelay = delay;
            return this;
        }

        public Builder process(ProcessConfig process) {
            this.process = process;
            return this;
        }

        /** Set the scheduler backend (typed). */
        public Builder scheduler(Scheduler scheduler) {
            this.scheduler = scheduler != null ? scheduler : Scheduler.DEFAULT;
            return this;
        }

        /**
         * Set the scheduler backend from its wire value (e.g. {@code "hostfactory"}).
         *
         * @throws IllegalArgumentException if the value is not a recognised scheduler
         */
        public Builder scheduler(String scheduler) {
            this.scheduler = Scheduler.fromWire(scheduler);
            return this;
        }

        public OrbClient build() throws Exception {
            // Set up process manager if configured
            SubprocessManager pm = null;
            String effectiveSocketPath = socketPath;

            if (process != null) {
                if (effectiveSocketPath == null || effectiveSocketPath.isEmpty()) {
                    effectiveSocketPath = process.getSocketPath();
                    if (effectiveSocketPath == null || effectiveSocketPath.isEmpty()) {
                        effectiveSocketPath = "/tmp/orb-java-" + ProcessHandle.current().pid() + ".sock";
                        process.setSocketPath(effectiveSocketPath);
                    }
                }
                pm = new SubprocessManager(process);
                pm.start();
            }

            // Build HTTP client
            String effectiveBaseUrl = (effectiveSocketPath != null && !effectiveSocketPath.isEmpty())
                    ? "http://localhost"
                    : baseUrl;

            RawHttpClient httpClient = new RawHttpClient(
                    effectiveSocketPath, effectiveBaseUrl, Duration.ofSeconds(10), timeout);
            httpClient.setMaxRetries(maxRetries);
            httpClient.setBaseDelay(retryBaseDelay);

            // Wire auth as a per-request provider so it is invoked on EVERY request
            // (including SSE streams), never captured once at build time.
            //
            //  - SigV4 requires per-request signing because Authorization must cover
            //    the full URI, canonical headers, and body hash.
            //  - Bearer (and any AuthStrategy) is invoked per request too, so a
            //    dynamic/refreshing token supplier actually takes effect and a
            //    rotated token is not frozen into stale default headers.
            if (auth instanceof AwsSigV4Auth sigV4Auth) {
                httpClient.setPerRequestSigner(
                        (method, uri, body) -> sigV4Auth.signRequest(method, uri, body));
            } else if (auth != null && auth != AuthStrategy.NONE) {
                final AuthStrategy authStrategy = auth;
                httpClient.setPerRequestHeaderProvider(() -> {
                    Map<String, String> h = new LinkedHashMap<>();
                    try {
                        authStrategy.apply(h);
                    } catch (Exception e) {
                        // Fail loud: an auth strategy that cannot produce credentials
                        // must not silently send an unauthenticated request.
                        throw new OrbError("auth header provider failed: " + e.getMessage(), e);
                    }
                    return h;
                });
            }

            // Scheduler header
            if (scheduler != Scheduler.DEFAULT) {
                httpClient.addDefaultHeader("X-ORB-Scheduler", scheduler.wireValue());
            }

            ObjectMapper mapper = buildObjectMapper();

            return new OrbClient(httpClient, mapper, pm, scheduler);
        }

        private ObjectMapper buildObjectMapper() {
            ObjectMapper m = new ObjectMapper();
            m.registerModule(new JavaTimeModule());
            m.registerModule(new org.openapitools.jackson.nullable.JsonNullableModule());
            m.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
            m.disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);
            m.setSerializationInclusion(JsonInclude.Include.NON_NULL);
            return m;
        }
    }
}
