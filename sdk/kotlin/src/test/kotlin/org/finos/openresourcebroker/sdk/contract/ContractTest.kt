/**
 * Contract tests for the ORB Kotlin SDK.
 *
 * Spawns a REAL ORB process over a UNIX domain socket and calls EVERY method
 * on the client, asserting:
 *   1. No route-level 404/405 errors (indicate a spec/client bug)
 *   2. Methods that return data return the expected shape
 *   3. Resource-level 404 is acceptable (route exists but resource not found)
 *
 * Distinguish route-level 404/405 from resource-level 404:
 *   - Route-level: URL path doesn't exist on server → SDK bug
 *   - Resource-level: route exists but resource not found → expected
 *
 * ORB is started with:
 *   <ORB_BINARY> -m orb --config <config.json> server start --foreground --api-only --socket-path <sock>
 *
 * Required env vars:
 *   ORB_BINARY — absolute path to the orb python binary (e.g. /path/to/.venv/bin/python)
 *   ORB_SRC    — optional path to the orb source root (for PYTHONPATH)
 */

package org.finos.openresourcebroker.sdk.contract

import com.google.gson.Gson
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.finos.openresourcebroker.sdk.auth.AuthOption
import org.finos.openresourcebroker.sdk.client.ClientConfig
import org.finos.openresourcebroker.sdk.client.OrbClient
import org.finos.openresourcebroker.sdk.model.*
import org.finos.openresourcebroker.sdk.client.OrbApiError
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import java.io.File
import java.nio.file.Files
import kotlin.time.Duration.Companion.seconds

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@TestMethodOrder(MethodOrderer.OrderAnnotation::class)
class ContractTest {

    private lateinit var client: OrbClient
    private lateinit var proc: Process
    private lateinit var tmpDir: File
    private lateinit var socketPath: String
    private val gson = Gson()

    companion object {
        private const val START_TIMEOUT_MS = 60_000L

        /** Resolved once — fails loudly if ORB_BINARY is not set in the environment. */
        private val ORB_BINARY: String = System.getenv("ORB_BINARY")
            ?: error(
                "ORB_BINARY environment variable is not set. " +
                "Set it to the absolute path of the orb python binary, e.g.: " +
                "export ORB_BINARY=/path/to/.venv/bin/python"
            )

        private val ORB_SRC = System.getenv("ORB_SRC") ?: ""
    }

    @BeforeAll
    fun startOrb() {
        tmpDir = Files.createTempDirectory("orb-contract-kt-").toFile()
        socketPath = "${tmpDir.absolutePath}/orb.sock"
        val configPath = "${tmpDir.absolutePath}/config.json"

        val config = mapOf(
            "version" to "2.0.0",
            "scheduler" to mapOf("type" to "default"),
            "provider" to mapOf(
                "providers" to listOf(
                    mapOf(
                        "name" to "aws-stub",
                        "type" to "aws",
                        "enabled" to true,
                        "config" to mapOf("region" to "us-east-1"),
                    )
                )
            ),
            "storage" to mapOf("type" to "json"),
            "server" to mapOf(
                "host" to "127.0.0.1",
                "port" to 19996,
                "working_dir" to tmpDir.absolutePath,
                "pid_file" to "${tmpDir.absolutePath}/orb.pid",
            ),
            "auth" to mapOf("type" to "none"),
            "logging" to mapOf("level" to "ERROR"),
        )

        File(configPath).writeText(gson.toJson(config))

        val env = ProcessBuilder().environment().toMutableMap()
        if (ORB_SRC.isNotEmpty()) env["PYTHONPATH"] = ORB_SRC
        env["ORB_LOG_LEVEL"] = "ERROR"

        proc = ProcessBuilder(
            ORB_BINARY, "-m", "orb",
            "--config", configPath,
            "server", "start", "--foreground", "--api-only",
            "--socket-path", socketPath,
        )
            .also { it.environment().putAll(env) }
            .redirectErrorStream(false)
            .start()

        // Drain stdout/stderr so the process never blocks on a full pipe
        Thread { try { proc.inputStream.copyTo(java.io.OutputStream.nullOutputStream()) } catch (_: Exception) {} }
            .also { it.isDaemon = true; it.start() }
        Thread { try { proc.errorStream.copyTo(java.io.OutputStream.nullOutputStream()) } catch (_: Exception) {} }
            .also { it.isDaemon = true; it.start() }

        println("ORB starting: PID=${proc.pid()}, socket=$socketPath")

        // Poll /health over UDS until ready
        waitForHealthy()

        // Create client
        client = runBlocking {
            OrbClient.create(
                ClientConfig(
                    socketPath = socketPath,
                    auth = AuthOption.None,
                    timeoutMs = 15_000L,
                )
            )
        }

        println("ORB healthy: PID=${proc.pid()}, socket=$socketPath")
    }

    @AfterAll
    fun stopOrb() {
        if (::client.isInitialized) client.close()
        if (::proc.isInitialized) {
            proc.destroy()
            proc.waitFor(10, java.util.concurrent.TimeUnit.SECONDS)
            if (proc.isAlive) proc.destroyForcibly()
        }
        if (::tmpDir.isInitialized) tmpDir.deleteRecursively()
        println("ORB stopped")
    }

    // ---------------------------------------------------------------------------
    // Helper: assertNotRouteLevelError
    // ---------------------------------------------------------------------------

    private fun assertNotRouteLevelError(err: Throwable, context: String) {
        if (err !is OrbApiError) return
        if (err.statusCode == 405) {
            fail<Nothing>("$context: HTTP 405 Method Not Allowed — route-level bug")
        }
        // Distinguish a route-level 404 from a resource-level 404. The orb returns
        // resource-level 404 as {"detail":"<Resource> not found"} and route-level
        // 404 as FastAPI's generic {"detail":"Not Found"} — SAME JSON shape. The
        // ONLY reliable discriminator is the exact generic message, matched on the
        // parsed error detail (err.message carries the parsed detail), not on the
        // presence/absence of the substring "detail" in the raw body.
        if (err.statusCode == 404 && isRouteLevelNotFound(err)) {
            fail<Nothing>("$context: HTTP 404 with generic 'Not Found' detail — route-level missing path")
        }
    }

    /**
     * True if a 404 is FastAPI's route-level "Not Found" (unknown path) rather than
     * a resource-level not-found. Matches the generic detail exactly (title-case),
     * which is what FastAPI emits for an unmatched route.
     */
    private fun isRouteLevelNotFound(err: OrbApiError): Boolean {
        val detail = parseDetail(err.body)
        return detail != null && detail.trim().equals("Not Found", ignoreCase = false)
    }

    private fun parseDetail(body: String?): String? {
        if (body.isNullOrBlank()) return null
        return try {
            val node: Map<*, *>? = gson.fromJson(body, Map::class.java)
            node?.get("detail") as? String
        } catch (_: Exception) {
            null
        }
    }

    // ---------------------------------------------------------------------------
    // System / Observability
    // ---------------------------------------------------------------------------

    @Test
    @Order(1)
    fun `health - GET health`(): Unit = runBlocking {
        // health() always returns the JSON body regardless of HTTP status code.
        // ORB returns 200 for healthy/degraded and 503 for unhealthy — in both cases
        // the server is responding and the route is correct.
        val result = client.health()
        assertNotNull(result["status"])
        val status = result["status"] as String
        assertTrue(
            status in listOf("healthy", "degraded", "unhealthy"),
            "Expected healthy/degraded/unhealthy, got: $status"
        )
        println("  health: $status")
    }

    @Test
    @Order(2)
    fun `info - GET info`(): Unit = runBlocking {
        val result = client.info()
        assertNotNull(result)
        println("  info.version: ${result["version"]}")
    }

    @Test
    @Order(3)
    fun `metrics - GET metrics`(): Unit = runBlocking {
        try {
            val result = client.metrics()
            // Metrics may be empty if Prometheus is not configured
            assertNotNull(result)
            println("  metrics: ${result.length} bytes${if (result.isEmpty()) " (empty - Prometheus not configured)" else ""}")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "metrics")
            // 404 acceptable if Prometheus metrics endpoint not configured
        }
    }

    @Test
    @Order(4)
    fun `getDashboardSummary - GET api v1 system dashboard`(): Unit = runBlocking {
        try {
            val result = client.getDashboardSummary()
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getDashboardSummary")
        }
    }

    @Test
    @Order(5)
    fun `getTelemetryStatus - GET api v1 observability telemetry`(): Unit = runBlocking {
        try {
            val result = client.getTelemetryStatus()
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getTelemetryStatus")
        }
    }

    @Test
    @Order(6)
    fun `getMe - GET api v1 me`(): Unit = runBlocking {
        try {
            val result = client.getMe()
            assertNotNull(result)
            println("  me: $result")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getMe")
            assertTrue(err.statusCode in listOf(200, 401))
            println("  getMe -> ${err.statusCode}")
        }
    }

    // ---------------------------------------------------------------------------
    // Providers
    // ---------------------------------------------------------------------------

    @Test
    @Order(10)
    fun `listProviders - GET api v1 providers`(): Unit = runBlocking {
        val result = client.listProviders()
        assertNotNull(result["providers"])
        val providers = result["providers"] as List<*>
        println("  providers: ${providers.size}")
    }

    @Test
    @Order(11)
    fun `getAllProviderSchemas - GET api v1 providers schemas`(): Unit = runBlocking {
        try {
            val result = client.getAllProviderSchemas()
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getAllProviderSchemas")
        }
    }

    @Test
    @Order(12)
    fun `getProviderSchema - GET api v1 providers name schema`(): Unit = runBlocking {
        try {
            val result = client.getProviderSchema("aws")
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getProviderSchema(aws)")
        }
    }

    @Test
    @Order(13)
    fun `getProvidersHealth - GET api v1 providers health`(): Unit = runBlocking {
        try {
            val result = client.getProvidersHealth()
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getProvidersHealth")
        }
    }

    // ---------------------------------------------------------------------------
    // Templates
    // ---------------------------------------------------------------------------

    private var createdTemplateId: String? = null

    @Test
    @Order(20)
    fun `listTemplates - GET api v1 templates`(): Unit = runBlocking {
        val result = client.listTemplates()
        assertNotNull(result.templates)
        println("  templates: ${result.templates?.size}")
    }

    @Test
    @Order(21)
    fun `createTemplate - POST api v1 templates`(): Unit = runBlocking {
        try {
            val result = client.createTemplate(
                TemplateCreateRequest(
                    templateId = "contract-test-kt-${System.currentTimeMillis()}",
                    name = "contract-test-kotlin",
                    description = "Created by Kotlin contract test",
                )
            )
            assertNotNull(result)
            createdTemplateId = result.templateId
            println("  created template: ${result.templateId}")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "createTemplate")
            println("  createTemplate -> ${err.statusCode} (acceptable)")
        }
    }

    @Test
    @Order(22)
    fun `getTemplate - GET api v1 templates id (nonexistent)`(): Unit = runBlocking {
        try {
            client.getTemplate("nonexistent-template-kotlin-xyz")
            // 200 is acceptable (e.g., lazy default)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getTemplate(nonexistent)")
            assertEquals(404, err.statusCode)
            println("  getTemplate(nonexistent) -> 404 (correct)")
        }
    }

    @Test
    @Order(23)
    fun `validateTemplate - POST api v1 templates validate`(): Unit = runBlocking {
        try {
            client.validateTemplate(mapOf("name" to "test", "provider_type" to "aws", "config" to emptyMap<String, Any>()))
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "validateTemplate")
        }
    }

    @Test
    @Order(24)
    fun `refreshTemplates - POST api v1 templates refresh`(): Unit = runBlocking {
        try {
            val result = client.refreshTemplates()
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "refreshTemplates")
        }
    }

    @Test
    @Order(25)
    fun `generateTemplates - POST api v1 templates generate`(): Unit = runBlocking {
        try {
            client.generateTemplates(GenerateTemplatesBody(provider = "aws-stub", allProviders = false))
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "generateTemplates")
        }
    }

    @Test
    @Order(26)
    fun `updateTemplate - PUT api v1 templates id (nonexistent)`(): Unit = runBlocking {
        val targetId = createdTemplateId ?: "nonexistent-xyz"
        try {
            client.updateTemplate(targetId, TemplateUpdateRequest(name = "updated", description = "updated"))
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "updateTemplate($targetId)")
            assertTrue(err.statusCode in listOf(404, 403, 422), "Expected 404/403/422, got ${err.statusCode}")
        }
    }

    @Test
    @Order(27)
    fun `deleteTemplate - DELETE api v1 templates id`(): Unit = runBlocking {
        val targetId = createdTemplateId ?: "nonexistent-xyz"
        try {
            client.deleteTemplate(targetId)
            createdTemplateId = null
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "deleteTemplate($targetId)")
            assertTrue(err.statusCode in listOf(404, 403), "Expected 404/403, got ${err.statusCode}")
        }
    }

    // ---------------------------------------------------------------------------
    // Machines
    // ---------------------------------------------------------------------------

    @Test
    @Order(30)
    fun `listMachines - GET api v1 machines`(): Unit = runBlocking {
        val result = client.listMachines()
        assertNotNull(result.machines)
        println("  machines: ${result.machines?.size}")
    }

    @Test
    @Order(31)
    fun `getMachine - GET api v1 machines id (nonexistent)`(): Unit = runBlocking {
        try {
            client.getMachine("nonexistent-machine-kt-xyz")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getMachine(nonexistent)")
            assertEquals(404, err.statusCode)
            println("  getMachine(nonexistent) -> 404 (correct)")
        }
    }

    @Test
    @Order(32)
    fun `requestMachines - POST api v1 machines request`(): Unit = runBlocking {
        // Always exercise POST /api/v1/machines/request — the flagship write op.
        // Use an existing template if configured, otherwise synthesize an ID so the
        // route is never silently skipped (matching the Java/.NET contract tests).
        // A resource-level error (400/403/404/422/500/503) is acceptable; a
        // route-level 404/405 is a bug.
        val templates = client.listTemplates().templates ?: emptyList()
        val templateId = templates.firstOrNull()?.templateId ?: "contract-test-synthetic-template-kt"
        try {
            val result = client.requestMachines(
                RequestMachinesRequest(templateId = templateId, count = 1)
            )
            assertNotNull(result)
            println("  requestMachines: ${result.requestId}")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "requestMachines")
            assertTrue(err.statusCode in listOf(400, 403, 404, 422, 500, 503),
                "Expected 400/403/404/422/500/503, got ${err.statusCode}")
            println("  requestMachines -> ${err.statusCode} (acceptable resource-level error)")
        }
    }

    @Test
    @Order(33)
    fun `returnMachines - POST api v1 machines return`(): Unit = runBlocking {
        try {
            client.returnMachines(ReturnMachinesRequest(machineIds = listOf("nonexistent-machine-kt")))
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "returnMachines")
        }
    }

    @Test
    @Order(34)
    fun `syncMachineStatus - GET api v1 machines id status`(): Unit = runBlocking {
        try {
            client.syncMachineStatus("nonexistent-machine-kt")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "syncMachineStatus")
            assertEquals(404, err.statusCode)
        }
    }

    @Test
    @Order(35)
    fun `getMachineMetrics - GET api v1 machines id metrics`(): Unit = runBlocking {
        try {
            client.getMachineMetrics("nonexistent-machine-kt")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getMachineMetrics")
            assertEquals(404, err.statusCode)
        }
    }

    @Test
    @Order(36)
    fun `purgeMachine - DELETE api v1 machines id`(): Unit = runBlocking {
        try {
            client.purgeMachine("nonexistent-machine-kt")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "purgeMachine")
            assertTrue(err.statusCode in listOf(404, 403))
        }
    }

    // ---------------------------------------------------------------------------
    // Requests
    // ---------------------------------------------------------------------------

    @Test
    @Order(40)
    fun `listRequests - GET api v1 requests`(): Unit = runBlocking {
        val result = client.listRequests()
        assertNotNull(result.requests)
        println("  requests: ${result.requests?.size}")
    }

    @Test
    @Order(41)
    fun `listReturnRequests - GET api v1 requests return`(): Unit = runBlocking {
        try {
            val result = client.listReturnRequests()
            assertNotNull(result.requests)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "listReturnRequests")
        }
    }

    @Test
    @Order(42)
    fun `getRequestStatus - GET api v1 requests id status (nonexistent)`(): Unit = runBlocking {
        try {
            val result = client.getRequestStatus("nonexistent-request-kt-xyz")
            assertNotNull(result)
            println("  getRequestStatus(nonexistent) -> 200 with synthetic data")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getRequestStatus(nonexistent)")
            assertTrue(err.statusCode in listOf(404, 400))
        }
    }

    @Test
    @Order(43)
    fun `getRequestTimeline - GET api v1 requests id timeline`(): Unit = runBlocking {
        try {
            client.getRequestTimeline("nonexistent-request-kt-xyz")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getRequestTimeline(nonexistent)")
            assertEquals(404, err.statusCode)
        }
    }

    @Test
    @Order(44)
    fun `batchGetRequestStatus - POST api v1 requests status`(): Unit = runBlocking {
        try {
            val result = client.batchGetRequestStatus(
                BatchRequestStatusBody(requestIds = listOf("nonexistent-1", "nonexistent-2"))
            )
            assertNotNull(result.requests)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "batchGetRequestStatus")
        }
    }

    @Test
    @Order(45)
    fun `cancelRequest - DELETE api v1 requests id (nonexistent)`(): Unit = runBlocking {
        try {
            client.cancelRequest("nonexistent-request-kt-xyz")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "cancelRequest(nonexistent)")
            assertTrue(err.statusCode in listOf(404, 403))
        }
    }

    @Test
    @Order(46)
    fun `purgeRequest - POST api v1 requests id purge (nonexistent)`(): Unit = runBlocking {
        try {
            client.purgeRequest("nonexistent-request-kt-xyz")
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "purgeRequest(nonexistent)")
            assertTrue(err.statusCode in listOf(404, 403))
        }
    }

    @Test
    @Order(47)
    @Timeout(20)
    fun `streamRequestStatus - GET api v1 requests id stream (nonexistent)`() {
        val result = java.util.concurrent.atomic.AtomicReference<String>("pending")
        val thread = Thread {
            try {
                runBlocking {
                    withTimeout(8.seconds) {
                        val event = client.streamRequestStatus(
                            "nonexistent-request-kt-xyz",
                            intervalSeconds = 1,
                            timeoutSeconds = 2,
                        ).firstOrNull()
                        result.set("got_event_${event?.status}")
                    }
                }
            } catch (e: OrbApiError) {
                assertNotRouteLevelError(e, "streamRequestStatus(nonexistent)")
                result.set("api_error_${e.statusCode}")
            } catch (_: kotlinx.coroutines.TimeoutCancellationException) {
                result.set("timeout_no_events")
            } catch (_: Exception) {
                result.set("stream_error")
            }
        }
        thread.isDaemon = true
        thread.start()
        thread.join(10000)
        if (thread.isAlive) {
            thread.interrupt()
            result.set("interrupted")
        }
        println("  streamRequestStatus -> ${result.get()} (route exists)")
    }

    // ---------------------------------------------------------------------------
    // SSE event bus
    // ---------------------------------------------------------------------------

    @Test
    @Order(50)
    @Timeout(15)
    fun `streamEvents - GET api v1 events (connect and abort)`() {
        // Open connection in a separate thread, give it 4s to connect then interrupt
        // We test that the route exists (no 404/405), not that events are received
        val result = java.util.concurrent.atomic.AtomicReference<String>("pending")
        val thread = Thread {
            try {
                runBlocking {
                    withTimeout(4.seconds) {
                        client.streamEvents().collect { _ ->
                            result.set("got_event")
                            return@collect
                        }
                    }
                }
            } catch (e: kotlinx.coroutines.TimeoutCancellationException) {
                result.set("connected_no_events")
            } catch (e: OrbApiError) {
                // 405, or 404 with FastAPI's generic "Not Found" detail = route-level
                // error = test failure. A resource-level 404 is acceptable.
                if (e.statusCode == 405 || (e.statusCode == 404 && isRouteLevelNotFound(e))) {
                    result.set("ROUTE_ERROR_${e.statusCode}")
                } else {
                    result.set("api_error_${e.statusCode}")
                }
            } catch (_: Exception) {
                result.set("connected_stream_error")
            }
        }
        thread.isDaemon = true
        thread.start()
        thread.join(6000) // max 6s wait
        if (thread.isAlive) {
            thread.interrupt()
            result.set("timeout_interrupted")
        }

        val r = result.get()
        assertFalse(r.startsWith("ROUTE_ERROR"), "Route-level error: $r")
        println("  streamEvents: $r")
    }

    // ---------------------------------------------------------------------------
    // Config
    // ---------------------------------------------------------------------------

    @Test
    @Order(60)
    fun `getFullConfig - GET api v1 config`(): Unit = runBlocking {
        try {
            val result = client.getFullConfig()
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getFullConfig")
        }
    }

    @Test
    @Order(61)
    fun `getConfigSources - GET api v1 config sources`(): Unit = runBlocking {
        try {
            val result = client.getConfigSources()
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getConfigSources")
        }
    }

    @Test
    @Order(62)
    fun `getConfigValue - GET api v1 config key`(): Unit = runBlocking {
        try {
            val result = client.getConfigValue("server.port")
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "getConfigValue(server.port)")
        }
    }

    @Test
    @Order(63)
    fun `validateConfig - POST api v1 config validate`(): Unit = runBlocking {
        try {
            client.validateConfig()
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "validateConfig")
        }
    }

    @Test
    @Order(64)
    fun `saveConfig - POST api v1 config save`(): Unit = runBlocking {
        try {
            client.saveConfig(SaveRequest())
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "saveConfig")
        }
    }

    @Test
    @Order(65)
    fun `setConfigValue - PUT api v1 config key`(): Unit = runBlocking {
        try {
            client.setConfigValue("logging.level", SetValueRequest(value = "ERROR"))
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "setConfigValue")
        }
    }

    // ---------------------------------------------------------------------------
    // Admin
    // ---------------------------------------------------------------------------

    @Test
    @Order(70)
    fun `initOrb - POST api v1 admin init`(): Unit = runBlocking {
        try {
            val result = client.initOrb(InitBody(confirm = "false", force = false, generateTemplates = false))
            assertNotNull(result)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "initOrb")
        }
    }

    @Test
    @Order(71)
    fun `cleanupDatabase - POST api v1 admin database cleanup`(): Unit = runBlocking {
        try {
            client.cleanupDatabase(CleanupDatabaseBody(confirm = "false", olderThanDays = 999))
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "cleanupDatabase")
        }
    }

    @Test
    @Order(72)
    fun `reloadConfig - POST api v1 admin reload-config`(): Unit = runBlocking {
        try {
            client.reloadConfig()
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "reloadConfig")
        }
    }

    @Test
    @Order(73)
    fun `wipeDatabase - POST api v1 admin database wipe (confirm false)`(): Unit = runBlocking {
        try {
            client.wipeDatabase(confirm = false)
        } catch (err: OrbApiError) {
            assertNotRouteLevelError(err, "wipeDatabase")
        }
    }

    // ---------------------------------------------------------------------------
    // Health polling helper
    // ---------------------------------------------------------------------------

    private fun waitForHealthy() {
        val deadline = System.currentTimeMillis() + START_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline) {
            Thread.sleep(300)
            if (!proc.isAlive) {
                throw RuntimeException("ORB process exited prematurely")
            }
            try {
                val result = pollHealthOverUds()
                if (result) {
                    println("ORB is healthy (socket=$socketPath)")
                    return
                }
            } catch (_: Exception) {}
        }
        proc.destroyForcibly()
        throw RuntimeException("ORB did not become healthy within ${START_TIMEOUT_MS}ms")
    }

    private fun pollHealthOverUds(): Boolean {
        val factory = org.finos.openresourcebroker.sdk.transport.UdsSocketFactory(socketPath)
        val httpClient = okhttp3.OkHttpClient.Builder()
            .socketFactory(factory)
            .connectTimeout(java.time.Duration.ofMillis(2000))
            .readTimeout(java.time.Duration.ofMillis(2000))
            .build()
        val req = okhttp3.Request.Builder().url("http://localhost/health").get().build()
        return httpClient.newCall(req).execute().use { resp ->
            // ORB returns HTTP 200 for healthy/degraded and HTTP 503 for unhealthy, but in
            // all three cases the server is UP and accepting requests. Accepting any response
            // that contains the ORB "status" field means the API is ready for testing.
            val body = resp.body?.string() ?: return false
            body.contains("\"status\"")
        }
    }
}
