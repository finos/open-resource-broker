/**
 * ORB Kotlin SDK — Public Client
 *
 * Covers all 44 operations from sdk/spec/openapi.json.
 * Uses generated models from the generated/ source set for typed request/response shapes.
 *
 * Two operating modes:
 *   - spawn: client starts ORB as a child process (UDS transport)
 *   - remote: client connects to an existing ORB instance (TCP/HTTPS)
 *
 * Five hand-written layers:
 *   1. SubprocessManager (process/)
 *   2. UdsSocketFactory (transport/)
 *   3. RetryInterceptor (transport/)
 *   4. AuthInterceptor (auth/)
 *   5. SseReader (sse/)
 */

package org.finos.openresourcebroker.sdk.client

import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.takeWhile
import kotlinx.coroutines.flow.transformWhile
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.finos.openresourcebroker.sdk.auth.AuthOption
import org.finos.openresourcebroker.sdk.auth.buildAuthInterceptor
import org.finos.openresourcebroker.sdk.model.*
import org.finos.openresourcebroker.sdk.process.ProcessConfig
import org.finos.openresourcebroker.sdk.process.SubprocessManager
import org.finos.openresourcebroker.sdk.process.tempSocketPath
import org.finos.openresourcebroker.sdk.sse.*
import org.finos.openresourcebroker.sdk.transport.RetryConfig
import org.finos.openresourcebroker.sdk.transport.RetryInterceptor
import org.finos.openresourcebroker.sdk.transport.UdsSocketFactory
import java.io.InputStream
import java.time.Duration
import java.util.concurrent.TimeUnit

private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

/**
 * Client configuration.
 */
data class ClientConfig(
    /** Base URL for remote mode (default: http://localhost:8000) */
    val baseUrl: String = "http://localhost:8000",
    /** Authentication strategy (default: none) */
    val auth: AuthOption = AuthOption.None,
    /** HTTP timeout in ms (default: 30_000) */
    val timeoutMs: Long = 30_000L,
    /** Retry configuration */
    val retry: RetryConfig = RetryConfig(),
    /** If set, start and manage an ORB subprocess */
    val process: ProcessConfig? = null,
    /** UNIX socket path for UDS mode without managed subprocess */
    val socketPath: String = "",
    /** Scheduler backend; sends the X-ORB-Scheduler header when not [Scheduler.DEFAULT] */
    val scheduler: Scheduler = Scheduler.DEFAULT,
)

/**
 * Canonical filter/pagination parameters for [OrbClient.listRequests] — the set
 * shared with the Go/TypeScript/Java SDKs. All fields optional; nulls are omitted.
 */
data class ListRequestsParams(
    val status: String? = null,
    val limit: Int? = null,
    val offset: Int? = null,
    val sync: Boolean? = null,
    val cursor: String? = null,
    val q: String? = null,
    val sort: String? = null,
    val providerName: String? = null,
    val providerType: String? = null,
    val templateId: String? = null,
    val requestType: String? = null,
    val filterExpressions: List<String>? = null,
)

/**
 * Canonical filter/pagination parameters for [OrbClient.listReturnRequests] — the
 * set shared with the Go/TypeScript/Java SDKs. All fields optional; nulls omitted.
 */
data class ListReturnRequestsParams(
    val limit: Int? = null,
    val offset: Int? = null,
    val cursor: String? = null,
    val q: String? = null,
    val sort: String? = null,
    val providerName: String? = null,
    val providerType: String? = null,
    val filterExpressions: List<String>? = null,
)

/**
 * High-level event emitted by [OrbClient.streamRequestStatus].
 */
data class StreamEvent(
    val requestId: String,
    val status: String,
    val message: String? = null,
    val requestedCount: Int? = null,
    val successfulCount: Int? = null,
    val failedCount: Int? = null,
    val machines: List<StreamMachine> = emptyList(),
)

data class StreamMachine(
    val machineId: String,
    val name: String? = null,
    val status: String? = null,
    val result: String? = null,
    val privateIp: String? = null,
    val publicIp: String? = null,
    val launchTime: String? = null,
    val message: String? = null,
)

// ---------------------------------------------------------------------------
// OrbClient
// ---------------------------------------------------------------------------

class OrbClient private constructor(
    private val http: OkHttpClient,
    /** A separate OkHttpClient with no retry interceptor for endpoints that manage their own error semantics. */
    private val httpNoRetry: OkHttpClient,
    private val baseUrl: String,
    private val scheduler: Scheduler,
    private val proc: SubprocessManager?,
    private val gson: Gson,
) {

    companion object {
        /**
         * Create and (optionally) start an OrbClient.
         * If [config].process is set, the ORB subprocess is started here.
         */
        suspend fun create(config: ClientConfig = ClientConfig()): OrbClient {
            val resolvedSocketPath = when {
                config.socketPath.isNotEmpty() -> config.socketPath
                config.process != null && config.process.socketPath.isNotEmpty() ->
                    config.process.socketPath
                config.process != null -> tempSocketPath()
                else -> ""
            }

            var proc: SubprocessManager? = null
            if (config.process != null) {
                val procCfg = config.process.copy(socketPath = resolvedSocketPath)
                proc = SubprocessManager(procCfg)
                proc.start()
            }

            val effectiveBaseUrl = if (resolvedSocketPath.isNotEmpty()) {
                "http://localhost"
            } else {
                config.baseUrl
            }

            val clientBuilder = OkHttpClient.Builder()
                .connectTimeout(Duration.ofMillis(config.timeoutMs))
                .readTimeout(Duration.ofMillis(config.timeoutMs))
                .callTimeout(Duration.ofMillis(config.timeoutMs))

            // Layer 2: UDS socket factory
            if (resolvedSocketPath.isNotEmpty()) {
                clientBuilder.socketFactory(UdsSocketFactory(resolvedSocketPath))
            }

            // Layer 4: auth interceptor (applied before retry so 401 is not retried)
            val authInterceptor = buildAuthInterceptor(config.auth)
            authInterceptor?.let { clientBuilder.addInterceptor(it) }

            // Layer 3: retry interceptor (main client)
            clientBuilder.addInterceptor(RetryInterceptor(config.retry))

            // No-retry client: same as the main client but without RetryInterceptor.
            // Used for endpoints (like /health) where non-2xx responses are semantically
            // valid and must not be retried or wrapped in an IOException.
            val noRetryBuilder = OkHttpClient.Builder()
                .connectTimeout(Duration.ofMillis(config.timeoutMs))
                .readTimeout(Duration.ofMillis(config.timeoutMs))
                .callTimeout(Duration.ofMillis(config.timeoutMs))
            if (resolvedSocketPath.isNotEmpty()) {
                noRetryBuilder.socketFactory(UdsSocketFactory(resolvedSocketPath))
            }
            authInterceptor?.let { noRetryBuilder.addInterceptor(it) }

            val gson = GsonBuilder().serializeNulls().create()

            return OrbClient(
                http = clientBuilder.build(),
                httpNoRetry = noRetryBuilder.build(),
                baseUrl = effectiveBaseUrl,
                scheduler = config.scheduler,
                proc = proc,
                gson = gson,
            )
        }
    }

    /**
     * Stop the managed subprocess (if any) and release resources.
     */
    fun close() {
        proc?.stop()
        http.dispatcher.executorService.shutdown()
        http.connectionPool.evictAll()
        httpNoRetry.dispatcher.executorService.shutdown()
        httpNoRetry.connectionPool.evictAll()
    }

    /**
     * True if the managed process (if any) is currently healthy.
     */
    val healthy: Boolean get() = proc?.healthy ?: true

    // ---------------------------------------------------------------------------
    // HTTP helpers
    // ---------------------------------------------------------------------------

    private fun checkHealth() {
        if (proc != null && !proc.healthy) {
            throw OrbUnavailableError("managed ORB process is unhealthy")
        }
    }

    private fun schedulerHeaders(): Map<String, String> =
        if (scheduler != Scheduler.DEFAULT)
            mapOf("X-ORB-Scheduler" to scheduler.wireValue)
        else emptyMap()

    private fun get(path: String, params: Map<String, String> = emptyMap()): Response {
        checkHealth()
        val urlBuilder = "$baseUrl$path".toHttpUrl().newBuilder()
        params.forEach { (k, v) -> urlBuilder.addQueryParameter(k, v) }
        val req = Request.Builder()
            .url(urlBuilder.build())
            .get()
            .header("Accept", "application/json")
            .also { rb -> schedulerHeaders().forEach { (k, v) -> rb.header(k, v) } }
            .build()
        return http.newCall(req).execute()
    }

    private fun post(path: String, body: Any? = null): Response {
        checkHealth()
        val jsonBody = (if (body != null) gson.toJson(body) else "{}").toRequestBody(JSON_MEDIA_TYPE)
        val req = Request.Builder()
            .url("$baseUrl$path")
            .post(jsonBody)
            .header("Accept", "application/json")
            .header("Content-Type", "application/json")
            .also { rb -> schedulerHeaders().forEach { (k, v) -> rb.header(k, v) } }
            .build()
        return http.newCall(req).execute()
    }

    private fun put(path: String, body: Any? = null): Response {
        checkHealth()
        val jsonBody = (if (body != null) gson.toJson(body) else "{}").toRequestBody(JSON_MEDIA_TYPE)
        val req = Request.Builder()
            .url("$baseUrl$path")
            .put(jsonBody)
            .header("Accept", "application/json")
            .header("Content-Type", "application/json")
            .also { rb -> schedulerHeaders().forEach { (k, v) -> rb.header(k, v) } }
            .build()
        return http.newCall(req).execute()
    }

    private fun delete(path: String): Response {
        checkHealth()
        val req = Request.Builder()
            .url("$baseUrl$path")
            .delete()
            .header("Accept", "application/json")
            .also { rb -> schedulerHeaders().forEach { (k, v) -> rb.header(k, v) } }
            .build()
        return http.newCall(req).execute()
    }

    private fun handleResponse(resp: Response): String {
        val body = resp.body?.string() ?: ""
        if (resp.code >= 400) {
            val requestId = requestIdFromHeaders(resp)
            resp.close()
            throw apiError(resp.code, body, requestId)
        }
        return body
    }

    /** Extract the server-assigned request ID from response headers, or null. */
    private fun requestIdFromHeaders(resp: Response): String? {
        for (h in listOf("x-request-id", "x-correlation-id")) {
            val v = resp.header(h)
            if (!v.isNullOrEmpty()) return v
        }
        return null
    }

    /**
     * Build the most specific typed [OrbApiError] for an HTTP error response,
     * extracting the machine-readable error code and message from the ORB error
     * body: `{"error": {"code": "...", "message": "..."}}` or `{"detail": "..."}`.
     */
    private fun apiError(status: Int, body: String, requestId: String?): OrbApiError {
        var code: String? = null
        var message: String = body.ifBlank { "HTTP $status" }
        try {
            val node: Map<*, *>? = gson.fromJson(body, Map::class.java)
            val err = node?.get("error")
            if (err is Map<*, *>) {
                code = err["code"]?.toString()
                (err["message"] as? String)?.let { message = it }
            } else {
                val detail = node?.get("detail")
                if (detail is String) message = detail
                else if (detail != null) message = gson.toJson(detail)
            }
        } catch (_: Exception) {
            // Non-JSON body — keep the raw body as the message.
        }
        return OrbApiError.forStatus(status, message, code, requestId, body)
    }

    private inline fun <reified T> parseJson(json: String): T =
        gson.fromJson(json, object : TypeToken<T>() {}.type)

    private inline fun <reified T> execute(response: Response): T {
        val json = handleResponse(response)
        return parseJson(json)
    }

    private fun executeRaw(response: Response): Map<String, Any?> {
        val json = handleResponse(response)
        @Suppress("UNCHECKED_CAST")
        return parseJson<Map<String, Any?>>(json)
    }

    // ---------------------------------------------------------------------------
    // System / Observability — 4 operations
    // ---------------------------------------------------------------------------

    /** healthCheck — GET /health
     *
     * Returns the health status regardless of HTTP status code.
     * ORB returns 200 for healthy/degraded and 503 for unhealthy, but in all cases
     * the body contains valid JSON with the health status fields. Uses [httpNoRetry]
     * so the 503 is returned to the caller rather than being retried until IOException.
     */
    suspend fun health(): Map<String, Any?> {
        checkHealth()
        val req = Request.Builder()
            .url("$baseUrl/health")
            .get()
            .header("Accept", "application/json")
            .also { rb -> schedulerHeaders().forEach { (k, v) -> rb.header(k, v) } }
            .build()
        // Use the no-retry client: 503 from /health is semantically valid (server is
        // up but reports itself unhealthy) and must not cause a retry loop.
        val resp = httpNoRetry.newCall(req).execute()
        val body = resp.body?.string() ?: "{}"
        resp.close()
        return parseJson(body)
    }

    /** getServiceInfo — GET /info */
    suspend fun info(): Map<String, Any?> {
        val resp = get("/info")
        return executeRaw(resp)
    }

    /** getMetrics — GET /metrics */
    suspend fun metrics(): String {
        checkHealth()
        val req = Request.Builder()
            .url("$baseUrl/metrics")
            .get()
            .header("Accept", "text/plain")
            .also { rb -> schedulerHeaders().forEach { (k, v) -> rb.header(k, v) } }
            .build()
        val resp = http.newCall(req).execute()
        if (resp.code >= 400) {
            val body = resp.body?.string() ?: ""
            val requestId = requestIdFromHeaders(resp)
            resp.close()
            throw apiError(resp.code, body, requestId)
        }
        return resp.body?.string() ?: ""
    }

    /** getDashboardSummary — GET /api/v1/system/dashboard */
    suspend fun getDashboardSummary(): Map<String, Any?> {
        val resp = get("/api/v1/system/dashboard")
        return executeRaw(resp)
    }

    // ---------------------------------------------------------------------------
    // Templates — 8 operations
    // ---------------------------------------------------------------------------

    /** listTemplates — GET /api/v1/templates/ */
    suspend fun listTemplates(): TemplateListResponse = execute(get("/api/v1/templates/"))

    /** getTemplate — GET /api/v1/templates/{template_id} */
    suspend fun getTemplate(templateId: String): TemplateItem =
        execute(get("/api/v1/templates/${encode(templateId)}"))

    /** createTemplate — POST /api/v1/templates/ */
    suspend fun createTemplate(body: TemplateCreateRequest): TemplateMutationResponse =
        execute(post("/api/v1/templates/", body))

    /** updateTemplate — PUT /api/v1/templates/{template_id} */
    suspend fun updateTemplate(templateId: String, body: TemplateUpdateRequest): TemplateMutationResponse =
        execute(put("/api/v1/templates/${encode(templateId)}", body))

    /** deleteTemplate — DELETE /api/v1/templates/{template_id} */
    suspend fun deleteTemplate(templateId: String): Map<String, Any?> =
        executeRaw(delete("/api/v1/templates/${encode(templateId)}"))

    /** validateTemplate — POST /api/v1/templates/validate */
    suspend fun validateTemplate(body: Any): Map<String, Any?> =
        executeRaw(post("/api/v1/templates/validate", body))

    /** refreshTemplates — POST /api/v1/templates/refresh */
    suspend fun refreshTemplates(): TemplateListResponse = execute(post("/api/v1/templates/refresh"))

    /** generateTemplates — POST /api/v1/templates/generate */
    suspend fun generateTemplates(body: GenerateTemplatesBody): TemplateListResponse =
        execute(post("/api/v1/templates/generate", body))

    // ---------------------------------------------------------------------------
    // Machines — 8 operations
    // ---------------------------------------------------------------------------

    /** listMachines — GET /api/v1/machines/ */
    suspend fun listMachines(
        status: String? = null,
        requestId: String? = null,
        limit: Int? = null,
        offset: Int? = null,
    ): MachineListResponse {
        val params = buildMap<String, String> {
            status?.let { put("status", it) }
            requestId?.let { put("request_id", it) }
            limit?.let { put("limit", it.toString()) }
            offset?.let { put("offset", it.toString()) }
        }
        return execute(get("/api/v1/machines/", params))
    }

    /** getMachine — GET /api/v1/machines/{machine_id} */
    suspend fun getMachine(machineId: String): MachineItem =
        execute(get("/api/v1/machines/${encode(machineId)}"))

    /** requestMachines — POST /api/v1/machines/request */
    suspend fun requestMachines(body: RequestMachinesRequest): RequestOperationResponse =
        execute(post("/api/v1/machines/request", body))

    /** returnMachines — POST /api/v1/machines/return */
    suspend fun returnMachines(body: ReturnMachinesRequest): RequestOperationResponse =
        execute(post("/api/v1/machines/return", body))

    /** syncMachineStatus — GET /api/v1/machines/{machine_id}/status */
    suspend fun syncMachineStatus(machineId: String): MachineListResponse =
        execute(get("/api/v1/machines/${encode(machineId)}/status"))

    /** getMachineMetrics — GET /api/v1/machines/{machine_id}/metrics */
    suspend fun getMachineMetrics(machineId: String, range: String? = null): Map<String, Any?> {
        val params = buildMap<String, String> { range?.let { put("range", it) } }
        return executeRaw(get("/api/v1/machines/${encode(machineId)}/metrics", params))
    }

    /** purgeMachine — DELETE /api/v1/machines/{machine_id} */
    suspend fun purgeMachine(machineId: String): Map<String, Any?> =
        executeRaw(delete("/api/v1/machines/${encode(machineId)}"))

    // ---------------------------------------------------------------------------
    // Requests — 10 operations
    // ---------------------------------------------------------------------------

    /**
     * listRequests — GET /api/v1/requests/
     *
     * Returns the typed [RequestStatusResponse] (matching the Java/.NET SDKs)
     * rather than an untyped Map. Exposes the canonical filter set shared with the
     * Go/TypeScript/Java SDKs via [ListRequestsParams].
     */
    suspend fun listRequests(params: ListRequestsParams = ListRequestsParams()): RequestStatusResponse {
        val query = buildMap<String, String> {
            params.status?.let { put("status", it) }
            params.limit?.let { put("limit", it.toString()) }
            params.offset?.let { put("offset", it.toString()) }
            params.sync?.let { put("sync", it.toString()) }
            params.cursor?.let { put("cursor", it) }
            params.q?.let { put("q", it) }
            params.sort?.let { put("sort", it) }
            params.providerName?.let { put("provider_name", it) }
            params.providerType?.let { put("provider_type", it) }
            params.templateId?.let { put("template_id", it) }
            params.requestType?.let { put("request_type", it) }
            params.filterExpressions?.takeIf { it.isNotEmpty() }
                ?.let { put("filter_expressions", it.joinToString(",")) }
        }
        return execute(get("/api/v1/requests/", query))
    }

    /**
     * listReturnRequests — GET /api/v1/requests/return
     *
     * Returns the typed [RequestStatusResponse]. Exposes the canonical filter set
     * shared with the Go/TypeScript/Java SDKs via [ListReturnRequestsParams].
     */
    suspend fun listReturnRequests(
        params: ListReturnRequestsParams = ListReturnRequestsParams(),
    ): RequestStatusResponse {
        val query = buildMap<String, String> {
            params.limit?.let { put("limit", it.toString()) }
            params.offset?.let { put("offset", it.toString()) }
            params.cursor?.let { put("cursor", it) }
            params.q?.let { put("q", it) }
            params.sort?.let { put("sort", it) }
            params.providerName?.let { put("provider_name", it) }
            params.providerType?.let { put("provider_type", it) }
            params.filterExpressions?.takeIf { it.isNotEmpty() }
                ?.let { put("filter_expressions", it.joinToString(",")) }
        }
        return execute(get("/api/v1/requests/return", query))
    }

    /** getRequestStatus — GET /api/v1/requests/{request_id}/status */
    suspend fun getRequestStatus(requestId: String, verbose: Boolean = false): RequestStatusResponse {
        val params = if (verbose) mapOf("verbose" to "true") else emptyMap()
        return execute(get("/api/v1/requests/${encode(requestId)}/status", params))
    }

    /** getRequest — GET /api/v1/requests/{request_id} */
    suspend fun getRequest(requestId: String, verbose: Boolean = false): RequestStatusResponse {
        val params = if (verbose) mapOf("verbose" to "true") else emptyMap()
        return execute(get("/api/v1/requests/${encode(requestId)}", params))
    }

    /** getRequestTimeline — GET /api/v1/requests/{request_id}/timeline */
    suspend fun getRequestTimeline(requestId: String): Map<String, Any?> =
        executeRaw(get("/api/v1/requests/${encode(requestId)}/timeline"))

    /**
     * batchGetRequestStatus — POST /api/v1/requests/status
     *
     * Returns the typed [RequestStatusResponse] (matching the Java/.NET SDKs).
     */
    suspend fun batchGetRequestStatus(body: BatchRequestStatusBody): RequestStatusResponse =
        execute(post("/api/v1/requests/status", body))

    /**
     * cancelRequest — DELETE /api/v1/requests/{request_id}
     *
     * @param reason optional cancellation reason (the spec's DELETE query param)
     */
    suspend fun cancelRequest(requestId: String, reason: String? = null): Map<String, Any?> {
        val path = "/api/v1/requests/${encode(requestId)}" +
                if (reason != null) "?reason=${encode(reason)}" else ""
        return executeRaw(delete(path))
    }

    /** purgeRequest — POST /api/v1/requests/{request_id}/purge */
    suspend fun purgeRequest(requestId: String): Map<String, Any?> =
        executeRaw(post("/api/v1/requests/${encode(requestId)}/purge"))

    /**
     * streamRequest
     * GET /api/v1/requests/{request_id}/stream
     *
     * Returns a Flow of [StreamEvent] objects.
     * Reconnects with back-off if the connection is dropped.
     */
    fun streamRequestStatus(
        requestId: String,
        intervalSeconds: Int = 2,
        timeoutSeconds: Int = 300,
    ): Flow<StreamEvent> {
        val self = this
        return sseStream(
            connect = { lastEventId ->
                self.openSseStream(
                    "/api/v1/requests/${encode(requestId)}/stream" +
                            "?interval=$intervalSeconds&timeout=$timeoutSeconds",
                    lastEventId,
                )
            }
        )
            .takeWhile { frame -> !frame.isSentinel() }
            .map { frame -> parseStreamEvent(frame) }
            .filter { it != null }
            .map { it!! }
            // Emit the terminal event, THEN stop. transformWhile emits every event
            // and continues only while the status is non-terminal — so the terminal
            // StreamEvent (completed/failed/etc.) is delivered as the last emission
            // before the flow completes. Matches the Go/TS/Java SDK contract, which
            // all deliver the terminal event and then stop. A plain
            // `takeWhile { !terminal }` would drop the terminal event entirely.
            .transformWhile { event ->
                emit(event)
                event.status !in TERMINAL_STATUSES
            }
    }

    /**
     * Wait for a request to reach a terminal status.
     * Collects [streamRequestStatus] until a terminal status is seen.
     */
    suspend fun waitForCompletion(
        requestId: String,
        intervalSeconds: Int = 2,
        timeoutSeconds: Int = 300,
    ): StreamEvent? {
        var last: StreamEvent? = null
        // streamRequestStatus completes right after emitting the first terminal event
        // (see its transformWhile), so the last collected element IS that terminal
        // event. Returns null only if the stream ends without ever emitting one.
        streamRequestStatus(requestId, intervalSeconds, timeoutSeconds)
            .collect { event ->
                last = event
            }
        return last
    }

    /**
     * streamEvents — GET /api/v1/events/
     *
     * Global SSE event bus. Returns a Flow of raw [SseFrame] objects.
     */
    fun streamEvents(): Flow<SseFrame> {
        val self = this
        return sseStream(
            connect = { lastEventId -> self.openSseStream("/api/v1/events/", lastEventId) }
        )
    }

    // ---------------------------------------------------------------------------
    // Providers — 4 operations
    // ---------------------------------------------------------------------------

    /** listProviders — GET /api/v1/providers/ */
    suspend fun listProviders(): Map<String, Any?> = executeRaw(get("/api/v1/providers/"))

    /** getAllProviderSchemas — GET /api/v1/providers/schemas */
    suspend fun getAllProviderSchemas(): Map<String, Any?> =
        executeRaw(get("/api/v1/providers/schemas"))

    /** getProviderSchema — GET /api/v1/providers/{name}/schema */
    suspend fun getProviderSchema(name: String): Map<String, Any?> =
        executeRaw(get("/api/v1/providers/${encode(name)}/schema"))

    /** getProvidersHealth — GET /api/v1/providers/health */
    suspend fun getProvidersHealth(): Map<String, Any?> =
        executeRaw(get("/api/v1/providers/health"))

    // ---------------------------------------------------------------------------
    // Config — 7 operations
    // ---------------------------------------------------------------------------

    /** getFullConfig — GET /api/v1/config/ */
    suspend fun getFullConfig(source: String? = null): Map<String, Any?> {
        val params = buildMap<String, String> { source?.let { put("source", it) } }
        return executeRaw(get("/api/v1/config/", params))
    }

    /** getConfigSources — GET /api/v1/config/sources */
    suspend fun getConfigSources(): Map<String, Any?> =
        executeRaw(get("/api/v1/config/sources"))

    /** getConfigValue — GET /api/v1/config/{key} */
    suspend fun getConfigValue(key: String): Any? {
        val json = handleResponse(get("/api/v1/config/${encode(key)}"))
        return gson.fromJson(json, Any::class.java)
    }

    /** setConfigValue — PUT /api/v1/config/{key} */
    suspend fun setConfigValue(key: String, body: SetValueRequest): Any? {
        val json = handleResponse(put("/api/v1/config/${encode(key)}", body))
        return gson.fromJson(json, Any::class.java)
    }

    /** saveConfig — POST /api/v1/config/save */
    suspend fun saveConfig(body: SaveRequest = SaveRequest()): Any? {
        val json = handleResponse(post("/api/v1/config/save", body))
        return gson.fromJson(json, Any::class.java)
    }

    /** validateConfig — POST /api/v1/config/validate */
    suspend fun validateConfig(): Map<String, Any?> = executeRaw(post("/api/v1/config/validate"))

    // ---------------------------------------------------------------------------
    // Admin — 4 operations
    // ---------------------------------------------------------------------------

    /** wipeDatabase — POST /api/v1/admin/database/wipe */
    suspend fun wipeDatabase(confirm: Boolean): Map<String, Any?> =
        executeRaw(post("/api/v1/admin/database/wipe", mapOf("confirm" to confirm)))

    /** initOrb — POST /api/v1/admin/init */
    suspend fun initOrb(body: InitBody): Map<String, Any?> =
        executeRaw(post("/api/v1/admin/init", body))

    /** cleanupDatabase — POST /api/v1/admin/database/cleanup */
    suspend fun cleanupDatabase(body: CleanupDatabaseBody): Map<String, Any?> =
        executeRaw(post("/api/v1/admin/database/cleanup", body))

    /** reloadConfig — POST /api/v1/admin/reload-config */
    suspend fun reloadConfig(): Map<String, Any?> =
        executeRaw(post("/api/v1/admin/reload-config"))

    // ---------------------------------------------------------------------------
    // Me / Observability — 2 operations
    // ---------------------------------------------------------------------------

    /** getCurrentUser — GET /api/v1/me/ */
    suspend fun getMe(): Map<String, Any?> = executeRaw(get("/api/v1/me/"))

    /** getTelemetryStatus — GET /api/v1/observability/telemetry */
    suspend fun getTelemetryStatus(): Map<String, Any?> =
        executeRaw(get("/api/v1/observability/telemetry"))

    // ---------------------------------------------------------------------------
    // SSE helpers
    // ---------------------------------------------------------------------------

    private fun openSseStream(path: String, lastEventId: String?): InputStream {
        checkHealth()
        val reqBuilder = Request.Builder()
            .url("$baseUrl$path")
            .get()
            .header("Accept", "text/event-stream")
            .also { rb -> schedulerHeaders().forEach { (k, v) -> rb.header(k, v) } }

        if (lastEventId != null) {
            reqBuilder.header("Last-Event-ID", lastEventId)
        }

        // Use a separate client with no read/call timeout for SSE. newBuilder()
        // inherits callTimeout from the base builder, and callTimeout bounds the
        // whole streaming call — so a long-lived SSE stream would be aborted at the
        // 30s client timeout unless we explicitly clear BOTH here. Cancellation and
        // the endpoint's own timeout parameter bound the stream instead.
        val sseClient = http.newBuilder()
            .readTimeout(Duration.ZERO)
            .callTimeout(Duration.ZERO)
            .build()

        val resp = sseClient.newCall(reqBuilder.build()).execute()
        if (resp.code >= 400) {
            // Connect-time status >= 400 is a terminal typed error: no frame parse,
            // no reconnect for 4xx (sseStream reconnects only on 5xx).
            val body = resp.body?.string() ?: ""
            val requestId = requestIdFromHeaders(resp)
            resp.close()
            throw apiError(resp.code, body, requestId)
        }
        return resp.body?.byteStream()
            ?: throw OrbApiError(0, "empty response body for SSE stream")
    }

    private fun parseStreamEvent(frame: SseFrame): StreamEvent? {
        if (frame.isSentinel()) return null
        return try {
            val payload = gson.fromJson(frame.data, OrbSsePayload::class.java)
            val requests = payload.requests ?: return null
            requests.firstOrNull()?.let { req ->
                StreamEvent(
                    requestId = req.request_id,
                    status = req.status,
                    message = req.message,
                    requestedCount = req.requested_count,
                    successfulCount = req.successful_count,
                    failedCount = req.failed_count,
                    machines = (req.machines ?: emptyList()).map { m ->
                        StreamMachine(
                            machineId = m.machine_id,
                            name = m.name,
                            status = m.status,
                            result = m.result,
                            privateIp = m.private_ip,
                            publicIp = m.public_ip,
                            launchTime = m.launch_time,
                            message = m.message,
                        )
                    },
                )
            }
        } catch (_: Exception) {
            null
        }
    }

    // ---------------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------------

    /**
     * URL-encode a path segment. [java.net.URLEncoder] emits
     * `application/x-www-form-urlencoded` (space → `+`), which is wrong for a path
     * segment (a space must be `%20`, or the request addresses the wrong resource).
     * Convert `+` back to `%20` to match the Java SDK and correctly escape IDs.
     */
    private fun encode(value: String): String =
        java.net.URLEncoder.encode(value, "UTF-8").replace("+", "%20")
}
