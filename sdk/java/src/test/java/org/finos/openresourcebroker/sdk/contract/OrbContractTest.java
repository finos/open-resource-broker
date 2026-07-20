package org.finos.openresourcebroker.sdk.contract;

import com.fasterxml.jackson.databind.ObjectMapper;

import org.finos.openresourcebroker.sdk.client.OrbApiException;
import org.finos.openresourcebroker.sdk.client.OrbClient;
import org.finos.openresourcebroker.sdk.model.*;
import org.finos.openresourcebroker.sdk.process.ProcessConfig;
import org.finos.openresourcebroker.sdk.sse.SseFrame;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.Tag;

import java.io.*;
import java.net.UnixDomainSocketAddress;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Contract tests for the ORB Java SDK.
 *
 * <p>These tests spawn a REAL ORB process over a Unix domain socket and call
 * EVERY method on the client, asserting:
 * <ol>
 *   <li>No route-level 404/405 (those indicate a spec/client bug)
 *   <li>Methods that return data return the expected shape
 *   <li>Methods that expect missing resources return proper 404-for-resource
 *       (NOT a 404/405 for the route itself)
 * </ol>
 *
 * <p>Distinguish route-level 404/405 from resource-level 404:
 * <ul>
 *   <li>Route-level 404/405: the URL path itself doesn't exist on the server — SDK bug
 *   <li>Resource-level 404: the route exists but the resource was not found — expected
 * </ul>
 *
 * <p>ORB is started with:
 * {@code python -m orb --config <tmp-config.json> server start --foreground --api-only --socket-path <sock>}
 */
@Tag("contract")
class OrbContractTest {

    private static final Logger LOG = Logger.getLogger(OrbContractTest.class.getName());
    private static final Duration START_TIMEOUT = Duration.ofSeconds(45);
    private static final String ORB_BINARY = findOrbBinary();

    private static Path tmpDir;
    private static String socketPath;
    private static Process orbProcess;
    private static OrbClient client;

    // ──────────────────────────────────────────────────────────────────────
    // Test fixture setup / teardown
    // ──────────────────────────────────────────────────────────────────────

    @BeforeAll
    static void startOrb() throws Exception {
        tmpDir = Files.createTempDirectory("orb-contract-java-");
        socketPath = tmpDir.resolve("orb.sock").toString();
        Path configPath = tmpDir.resolve("config.json");

        // Minimal config: no-auth, json storage, aws provider with stub credentials
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("version", "2.0.0");
        config.put("scheduler", Map.of("type", "default"));
        config.put("provider", Map.of("providers", List.of(Map.of(
                "name", "aws-stub",
                "type", "aws",
                "enabled", true,
                "config", Map.of("region", "us-east-1")))));
        config.put("storage", Map.of("type", "json"));
        config.put("server", Map.of(
                "host", "127.0.0.1",
                "port", 19996,
                "working_dir", tmpDir.toString(),
                "pid_file", tmpDir.resolve("orb.pid").toString()));
        config.put("auth", Map.of("type", "none"));
        config.put("logging", Map.of("level", "ERROR"));

        new ObjectMapper().writeValue(configPath.toFile(), config);

        // Build command
        List<String> cmd = buildOrbCommand(configPath.toString(), socketPath);
        LOG.info("Starting ORB: " + String.join(" ", cmd));

        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.environment().put("ORB_LOG_LEVEL", "ERROR");
        pb.redirectOutput(ProcessBuilder.Redirect.DISCARD);
        pb.redirectError(ProcessBuilder.Redirect.DISCARD);
        orbProcess = pb.start();

        LOG.info("ORB PID: " + orbProcess.pid() + "  socket: " + socketPath);

        // Wait for healthy
        waitForHealthy(socketPath, START_TIMEOUT);

        LOG.info("ORB healthy — creating client");

        // Build client
        client = OrbClient.builder()
                .socketPath(socketPath)
                .timeout(Duration.ofSeconds(15))
                .maxRetries(1)
                .retryBaseDelay(Duration.ofMillis(100))
                .build();

        LOG.info("Client created — starting contract tests");
    }

    @AfterAll
    static void stopOrb() throws Exception {
        if (client != null) {
            try { client.close(); } catch (Exception ignored) {}
        }
        if (orbProcess != null) {
            orbProcess.destroy();
            orbProcess.waitFor(5, java.util.concurrent.TimeUnit.SECONDS);
            orbProcess.destroyForcibly();
        }
        if (tmpDir != null) {
            deleteDirectory(tmpDir.toFile());
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // System / Observability — 4 ops
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void health_GET_health() throws Exception {
        var result = client.health();
        assertNotNull(result, "health check should return a response");
        Object status = result.get("status");
        assertNotNull(status, "response should have a 'status' field");
        String s = status.toString();
        assertTrue(s.equals("healthy") || s.equals("degraded"),
                "status should be 'healthy' or 'degraded', got: " + s);
        LOG.info("  health: " + s);
    }

    @Test
    void info_GET_info() throws Exception {
        var result = client.info();
        assertNotNull(result, "info should return a response");
        LOG.info("  info.version: " + result.getOrDefault("version", "unknown"));
    }

    @Test
    void metrics_GET_metrics() throws Exception {
        // GET /metrics should return 200 (the route must exist).
        // Body may be empty when prometheus-client is not installed — that is acceptable.
        // Route-level 404/405 would be a bug; empty body is not.
        try {
            String result = client.metrics();
            assertNotNull(result, "metrics should return a string (may be empty without prometheus-client)");
            LOG.info("  metrics: " + result.length() + " bytes");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "metrics");
        }
    }

    @Test
    void getDashboardSummary_GET_api_v1_system_dashboard() throws Exception {
        try {
            var result = client.getDashboardSummary();
            assertNotNull(result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getDashboardSummary");
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Providers — 4 ops
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void listProviders_GET_api_v1_providers() throws Exception {
        var result = client.listProviders();
        assertNotNull(result, "listProviders should return a response");
        LOG.info("  providers: " + result);
    }

    @Test
    void getAllProviderSchemas_GET_api_v1_providers_schemas() throws Exception {
        try {
            var result = client.getAllProviderSchemas();
            assertNotNull(result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getAllProviderSchemas");
        }
    }

    @Test
    void getProviderSchema_GET_api_v1_providers_name_schema() throws Exception {
        try {
            var result = client.getProviderSchema("aws");
            assertNotNull(result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getProviderSchema(aws)");
        }
    }

    @Test
    void getProvidersHealth_GET_api_v1_providers_health() throws Exception {
        try {
            var result = client.getProvidersHealth();
            assertNotNull(result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getProvidersHealth");
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Templates — 8 ops
    // ──────────────────────────────────────────────────────────────────────

    private String createdTemplateId = null;

    @Test
    @Order(10)
    void listTemplates_GET_api_v1_templates() throws Exception {
        TemplateListResponse result = client.listTemplates(null, null, null);
        assertNotNull(result, "listTemplates should return a response");
        assertNotNull(result.getTemplates(), "templates list should not be null");
        LOG.info("  templates: " + result.getTemplates().size());
    }

    @Test
    @Order(11)
    void createTemplate_POST_api_v1_templates() throws Exception {
        var req = new TemplateCreateRequest();
        req.setTemplateId("contract-test-" + System.currentTimeMillis());
        req.setName("Java SDK Contract Test");
        req.setDescription("Created by Java contract test");

        try {
            var result = client.createTemplate(req);
            assertNotNull(result);
            createdTemplateId = result.getTemplateId();
            LOG.info("  created template: " + createdTemplateId);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "createTemplate");
            LOG.info("  createTemplate returned error (acceptable): " + e.getMessage());
        }
    }

    @Test
    @Order(12)
    void getTemplate_GET_api_v1_templates_id_nonexistent() throws Exception {
        try {
            client.getTemplate("nonexistent-template-id-xyz-java");
            fail("Expected OrbApiException for nonexistent template");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getTemplate(nonexistent)");
            assertEquals(404, e.getStatusCode(),
                    "nonexistent template should return 404: " + e.getMessage());
            LOG.info("  getTemplate(nonexistent) → 404 (correct)");
        }
    }

    @Test
    @Order(13)
    void validateTemplate_POST_api_v1_templates_validate() throws Exception {
        try {
            client.validateTemplate(Map.of("name", "test", "provider_type", "aws"));
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "validateTemplate");
        }
    }

    @Test
    @Order(14)
    void refreshTemplates_POST_api_v1_templates_refresh() throws Exception {
        try {
            var result = client.refreshTemplates();
            assertNotNull(result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "refreshTemplates");
        }
    }

    @Test
    @Order(15)
    void generateTemplates_POST_api_v1_templates_generate() throws Exception {
        var body = new GenerateTemplatesBody();
        body.setProvider("aws-stub");
        body.setAllProviders(false);
        try {
            client.generateTemplates(body);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "generateTemplates");
            // 500 acceptable if no real AWS creds
        }
    }

    @Test
    @Order(16)
    void updateTemplate_PUT_api_v1_templates_id() throws Exception {
        var req = new TemplateUpdateRequest();
        req.setName("Updated by Java Contract Test");
        req.setDescription("updated");
        try {
            client.updateTemplate("nonexistent-xyz", req);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "updateTemplate(nonexistent)");
            assertTrue(Set.of(404, 403, 422).contains(e.getStatusCode()),
                    "expected 404/403/422, got: " + e.getStatusCode());
        }
    }

    @Test
    @Order(17)
    void deleteTemplate_DELETE_api_v1_templates_id() throws Exception {
        try {
            client.deleteTemplate("nonexistent-xyz");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "deleteTemplate(nonexistent)");
            assertTrue(Set.of(404, 403).contains(e.getStatusCode()),
                    "expected 404/403, got: " + e.getStatusCode());
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Machines — 7 ops
    // ──────────────────────────────────────────────────────────────────────

    @Test
    @Order(20)
    void listMachines_GET_api_v1_machines() throws Exception {
        var result = client.listMachines(null, null, null, null, null);
        assertNotNull(result, "listMachines should return a response");
        assertNotNull(result.getMachines(), "machines list should not be null");
        LOG.info("  machines: " + result.getMachines().size());
    }

    @Test
    @Order(21)
    void getMachine_GET_api_v1_machines_id_nonexistent() throws Exception {
        try {
            client.getMachine("nonexistent-machine-id-xyz-java");
            fail("Expected OrbApiException for nonexistent machine");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getMachine(nonexistent)");
            assertEquals(404, e.getStatusCode(),
                    "nonexistent machine should return 404: " + e.getMessage());
            LOG.info("  getMachine(nonexistent) → 404 (correct)");
        }
    }

    @Test
    @Order(22)
    void requestMachines_POST_api_v1_machines_request() throws Exception {
        // Ensure at least one template exists so we can hit the real POST route.
        // (Without a template_id the server returns 422 Unprocessable Entity — but we
        // want to verify the route itself responds, not just that we passed a bad body.)
        TemplateListResponse templates = client.listTemplates(null, null, null);
        String templateId = null;

        if (templates.getTemplates() != null && !templates.getTemplates().isEmpty()) {
            templateId = templates.getTemplates().get(0).getTemplateId();
            LOG.info("  using existing template: " + templateId);
        } else {
            // Create a minimal template so the POST /api/v1/machines/request route is hit
            var createReq = new TemplateCreateRequest();
            createReq.setTemplateId("contract-request-machines-" + System.currentTimeMillis());
            createReq.setName("Contract Test — requestMachines probe");
            createReq.setDescription("Ephemeral template created to exercise POST /api/v1/machines/request");
            try {
                var created = client.createTemplate(createReq);
                templateId = created != null ? created.getTemplateId() : createReq.getTemplateId();
                LOG.info("  created probe template: " + templateId);
            } catch (OrbApiException createEx) {
                assertNotRouteLevelError(createEx, "createTemplate (pre-requestMachines)");
                // If template creation itself fails with a resource error, fall back to
                // a synthetic ID so we still hit the requestMachines route
                templateId = createReq.getTemplateId();
                LOG.info("  createTemplate error (" + createEx.getStatusCode() + ") — using id anyway: " + templateId);
            }
        }

        var req = new RequestMachinesRequest();
        req.setTemplateId(templateId);
        req.setCount(1);
        try {
            var result = client.requestMachines(req);
            assertNotNull(result);
            LOG.info("  requestMachines: " + result.getRequestId());
        } catch (OrbApiException e) {
            // Route-level 404/405 is a bug; resource-level errors are acceptable
            assertNotRouteLevelError(e, "requestMachines");
            // 400/422/403/500 all acceptable without real AWS credentials or provider config
            assertTrue(Set.of(400, 403, 422, 500, 503).contains(e.getStatusCode()),
                    "acceptable status without AWS: " + e.getStatusCode() + " " + e.getMessage());
            LOG.info("  requestMachines → " + e.getStatusCode() + " (resource-level error, route hit correctly)");
        }
    }

    @Test
    @Order(23)
    void returnMachines_POST_api_v1_machines_return() throws Exception {
        var req = new ReturnMachinesRequest();
        req.setMachineIds(List.of("nonexistent-machine-id"));
        try {
            client.returnMachines(req);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "returnMachines");
            // 404/400 expected for nonexistent machine
        }
    }

    @Test
    @Order(24)
    void syncMachineStatus_GET_api_v1_machines_id_status() throws Exception {
        try {
            client.syncMachineStatus("nonexistent-machine-id");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "syncMachineStatus(nonexistent)");
            assertEquals(404, e.getStatusCode());
        }
    }

    @Test
    @Order(25)
    void getMachineMetrics_GET_api_v1_machines_id_metrics() throws Exception {
        try {
            client.getMachineMetrics("nonexistent-machine-id");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getMachineMetrics(nonexistent)");
            assertEquals(404, e.getStatusCode());
        }
    }

    @Test
    @Order(26)
    void purgeMachine_DELETE_api_v1_machines_id() throws Exception {
        try {
            client.purgeMachine("nonexistent-machine-id");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "purgeMachine(nonexistent)");
            assertTrue(Set.of(404, 403).contains(e.getStatusCode()),
                    "expected 404/403, got: " + e.getStatusCode());
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Requests — 7 ops + SSE
    // ──────────────────────────────────────────────────────────────────────

    @Test
    @Order(30)
    void listRequests_GET_api_v1_requests() throws Exception {
        var result = client.listRequests();
        assertNotNull(result, "listRequests should return a response");
        assertNotNull(result.getRequests(), "requests list should not be null");
        LOG.info("  requests: " + result.getRequests().size());
    }

    @Test
    @Order(31)
    void listReturnRequests_GET_api_v1_requests_return() throws Exception {
        try {
            var result = client.listReturnRequests();
            assertNotNull(result);
            assertNotNull(result.getRequests());
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "listReturnRequests");
        }
    }

    @Test
    @Order(32)
    void getRequestStatus_GET_api_v1_requests_id_status_nonexistent() throws Exception {
        try {
            var result = client.getRequestStatus("nonexistent-request-id-xyz-java", null);
            // 200 with synthetic data is also acceptable
            assertNotNull(result);
            LOG.info("  getRequestStatus(nonexistent) → 200 with synthetic data");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getRequestStatus(nonexistent)");
            assertTrue(Set.of(404, 400).contains(e.getStatusCode()));
        }
    }

    @Test
    @Order(33)
    void batchGetRequestStatus_POST_api_v1_requests_status() throws Exception {
        var body = new BatchRequestStatusBody();
        body.setRequestIds(List.of("nonexistent-id-1", "nonexistent-id-2"));
        try {
            var result = client.batchGetRequestStatus(body);
            assertNotNull(result);
            assertNotNull(result.getRequests());
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "batchGetRequestStatus");
        }
    }

    @Test
    @Order(34)
    void getRequestTimeline_GET_api_v1_requests_id_timeline() throws Exception {
        try {
            client.getRequestTimeline("nonexistent-request-id-xyz-java");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getRequestTimeline(nonexistent)");
            assertEquals(404, e.getStatusCode());
        }
    }

    @Test
    @Order(35)
    void cancelRequest_DELETE_api_v1_requests_id_nonexistent() throws Exception {
        try {
            client.cancelRequest("nonexistent-request-id-xyz-java", null);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "cancelRequest(nonexistent)");
            assertTrue(Set.of(404, 403).contains(e.getStatusCode()),
                    "expected 404/403, got: " + e.getStatusCode());
        }
    }

    @Test
    @Order(36)
    void purgeRequest_POST_api_v1_requests_id_purge() throws Exception {
        try {
            client.purgeRequest("nonexistent-request-id-xyz-java");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "purgeRequest(nonexistent)");
            assertTrue(Set.of(404, 403).contains(e.getStatusCode()),
                    "expected 404/403, got: " + e.getStatusCode());
        }
    }

    @Test
    @Order(37)
    void streamRequestStatus_GET_api_v1_requests_id_stream_nonexistent() throws Exception {
        // A nonexistent request should either error quickly or return nothing — NOT hang
        AtomicInteger eventCount = new AtomicInteger(0);
        long start = System.currentTimeMillis();

        try {
            // Short timeout (3s) so we don't hang on tests
            client.streamRequestStatus(
                    "nonexistent-request-id-xyz-java",
                    1.0,  // interval
                    3.0,  // timeout
                    event -> eventCount.incrementAndGet());
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "streamRequestStatus(nonexistent)");
        }

        long elapsed = System.currentTimeMillis() - start;
        LOG.info("  streamRequestStatus(nonexistent): " + eventCount + " events, " + elapsed + "ms");
        // Should complete within a reasonable time (not hang forever)
        assertTrue(elapsed < 10_000, "stream should not hang: elapsed=" + elapsed + "ms");
    }

    @Test
    @Order(38)
    void streamEvents_GET_api_v1_events() throws Exception {
        // GET /api/v1/events/ is the global SSE event bus.
        // We open the stream in a background thread with a hard 8-second cap so we
        // don't hang the test suite.  Any events received are a bonus; the key
        // assertion is that the route exists and the connection is accepted
        // (no route-level 404/405).
        AtomicInteger eventCount = new AtomicInteger(0);
        final long[] capturedException = {0};
        long start = System.currentTimeMillis();

        Thread streamer = new Thread(() -> {
            try {
                client.streamEvents(frame -> {
                    eventCount.incrementAndGet();
                    String data = frame.data();
                    LOG.info("  streamEvents event: " + (data.length() > 80 ? data.substring(0, 80) + "…" : data));
                }, 5_000 /* ms timeout — inner deadline check */);
            } catch (OrbApiException e) {
                capturedException[0] = e.getStatusCode();
            } catch (Exception ignored) {
                // InterruptedException from the interrupt below is normal
            }
        }, "streamEvents-contract");
        streamer.setDaemon(true);
        streamer.start();
        streamer.join(8_000 /* hard cap */);
        if (streamer.isAlive()) {
            streamer.interrupt(); // safety valve
            streamer.join(1_000);
        }

        long elapsed = System.currentTimeMillis() - start;
        LOG.info("  streamEvents: " + eventCount + " event(s) in " + elapsed + "ms");

        if (capturedException[0] != 0) {
            // OrbApiException — check it's not a route-level error
            assertNotEquals(405, capturedException[0],
                    "streamEvents: HTTP 405 Method Not Allowed — route-level bug");
            assertNotEquals(404, capturedException[0],
                    "streamEvents: HTTP 404 Not Found — route-level bug");
        }

        // Should have returned within the hard cap
        assertTrue(elapsed < 12_000, "streamEvents should not hang: elapsed=" + elapsed + "ms");
    }

    // ──────────────────────────────────────────────────────────────────────
    // Observability — 1 op
    // ──────────────────────────────────────────────────────────────────────

    @Test
    @Order(40)
    void getTelemetryStatus_GET_api_v1_observability_telemetry() throws Exception {
        try {
            var result = client.getTelemetryStatus();
            assertNotNull(result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getTelemetryStatus");
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Me — 1 op
    // ──────────────────────────────────────────────────────────────────────

    @Test
    @Order(41)
    void getMe_GET_api_v1_me() throws Exception {
        try {
            var result = client.getMe();
            assertNotNull(result);
            LOG.info("  me: " + result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getMe");
            // 401 expected (no session) — but NOT a route-level error
            assertTrue(Set.of(200, 401).contains(e.getStatusCode()),
                    "getMe should return 200 or 401, got: " + e.getStatusCode());
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Admin — 4 ops
    // ──────────────────────────────────────────────────────────────────────

    @Test
    @Order(50)
    void initOrb_POST_api_v1_admin_init() throws Exception {
        var body = new InitBody();
        body.setForce(false);
        body.setConfirm("true");
        try {
            client.initOrb(body);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "initOrb");
        }
    }

    @Test
    @Order(51)
    void reloadConfig_POST_api_v1_admin_reload_config() throws Exception {
        try {
            client.reloadConfig();
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "reloadConfig");
        }
    }

    @Test
    @Order(52)
    void cleanupDatabase_POST_api_v1_admin_database_cleanup() throws Exception {
        var body = new CleanupDatabaseBody();
        body.setConfirm("true");
        body.setOlderThanDays(0);
        try {
            client.cleanupDatabase(body);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "cleanupDatabase");
        }
    }

    @Test
    @Order(53)
    void wipeDatabase_POST_api_v1_admin_database_wipe() throws Exception {
        // Only test that the route exists — do NOT actually wipe data without confirm
        try {
            client.wipeDatabase(Map.of("confirm", false));
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "wipeDatabase");
            // 400 (confirm=false) or 403 are acceptable
            assertTrue(Set.of(400, 403, 422).contains(e.getStatusCode()),
                    "expected 400/403/422 without confirm, got: " + e.getStatusCode());
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Config — 5 ops
    // ──────────────────────────────────────────────────────────────────────

    @Test
    @Order(60)
    void getFullConfig_GET_api_v1_config() throws Exception {
        try {
            var result = client.getFullConfig();
            assertNotNull(result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getFullConfig");
        }
    }

    @Test
    @Order(61)
    void getConfigSources_GET_api_v1_config_sources() throws Exception {
        try {
            var result = client.getConfigSources();
            assertNotNull(result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getConfigSources");
        }
    }

    @Test
    @Order(62)
    void validateConfig_POST_api_v1_config_validate() throws Exception {
        try {
            client.validateConfig(Map.of("scheduler", Map.of("type", "default")));
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "validateConfig");
        }
    }

    @Test
    @Order(63)
    void getConfigValue_GET_api_v1_config_key() throws Exception {
        try {
            client.getConfigValue("scheduler.type");
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "getConfigValue");
            // 404 is acceptable for a key that may not exist
        }
    }

    @Test
    @Order(64)
    void saveConfig_POST_api_v1_config_save() throws Exception {
        var body = new SaveRequest();
        body.setPath(tmpDir.resolve("saved-config.json").toString());
        try {
            client.saveConfig(body);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "saveConfig");
        }
    }

    @Test
    @Order(65)
    void setConfigValue_PUT_api_v1_config_key() throws Exception {
        // PUT /api/v1/config/{key} — set a runtime config value.
        // We use a benign key that the server accepts without side-effects.
        // 200 = accepted; 400/422/403 = rejected for value reasons; all acceptable.
        // 404/405 on the route itself = SDK bug.
        var body = new SetValueRequest();
        body.setValue("ERROR");
        try {
            var result = client.setConfigValue("logging.level", body);
            assertNotNull(result, "setConfigValue should return a response object");
            LOG.info("  setConfigValue(logging.level) → " + result);
        } catch (OrbApiException e) {
            assertNotRouteLevelError(e, "setConfigValue(logging.level)");
            // 400/422/403 are all acceptable — the route was reached
            assertTrue(Set.of(400, 403, 422).contains(e.getStatusCode()),
                    "setConfigValue: expected 400/403/422 on value rejection, got: "
                            + e.getStatusCode() + " " + e.getMessage());
            LOG.info("  setConfigValue → " + e.getStatusCode() + " (route hit, value rejected — acceptable)");
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Helpers
    // ──────────────────────────────────────────────────────────────────────

    /**
     * Assert that an error is NOT a route-level 404/405.
     * Resource-level 404 (resource doesn't exist) IS acceptable.
     * Route-level 404/405 means the URL path itself doesn't exist — that's a bug.
     */
    private static void assertNotRouteLevelError(OrbApiException e, String context) {
        // 405 is always a route-level error
        assertNotEquals(405, e.getStatusCode(),
                context + ": got HTTP 405 Method Not Allowed — route-level bug");

        // A 404 with no structured message is likely route-level
        if (e.getStatusCode() == 404) {
            String msg = e.getMessage();
            assertFalse(msg != null && msg.contains("Not Found") && e.getCode() == null
                            && !msg.contains("not found") && !msg.contains("HTTP 404"),
                    context + ": got HTTP 404 with unstructured 'Not Found' — likely route-level");
        }
    }

    private static String findOrbBinary() {
        // Check env var first
        String envBinary = System.getenv("ORB_BINARY");
        if (envBinary != null && !envBinary.isEmpty()) return envBinary;

        // Check if orb is on PATH
        for (String dir : System.getenv("PATH").split(File.pathSeparator)) {
            File f = new File(dir, "orb");
            if (f.canExecute()) return f.getAbsolutePath();
        }

        // Try python/python3
        for (String py : new String[]{"python3", "python"}) {
            for (String dir : System.getenv("PATH").split(File.pathSeparator)) {
                File f = new File(dir, py);
                if (f.canExecute()) return f.getAbsolutePath();
            }
        }

        return "orb"; // default
    }

    private static List<String> buildOrbCommand(String configPath, String socketPath) {
        List<String> cmd = new ArrayList<>();
        String binary = ORB_BINARY;

        if (binary.endsWith("python3") || binary.endsWith("python")) {
            cmd.add(binary);
            cmd.add("-m");
            cmd.add("orb");
        } else {
            cmd.add(binary);
        }

        cmd.add("--config");
        cmd.add(configPath);
        cmd.add("server");
        cmd.add("start");
        cmd.add("--foreground");
        cmd.add("--api-only");
        cmd.add("--socket-path");
        cmd.add(socketPath);

        return cmd;
    }

    private static void waitForHealthy(String socketPath, Duration timeout)
            throws InterruptedException, IOException {
        long deadline = System.currentTimeMillis() + timeout.toMillis();
        Exception lastErr = null;

        while (System.currentTimeMillis() < deadline) {
            Thread.sleep(200);
            try {
                UnixDomainSocketAddress addr = UnixDomainSocketAddress.of(socketPath);
                try (SocketChannel ch = SocketChannel.open(addr)) {
                    ch.configureBlocking(true);
                    var out = java.nio.channels.Channels.newOutputStream(ch);
                    var in  = java.nio.channels.Channels.newInputStream(ch);

                    String req = "GET /health HTTP/1.1\r\nHost: localhost\r\nAccept: application/json\r\nConnection: close\r\n\r\n";
                    out.write(req.getBytes(StandardCharsets.US_ASCII));
                    out.flush();

                    BufferedReader reader = new BufferedReader(
                            new InputStreamReader(in, StandardCharsets.UTF_8));
                    String statusLine = reader.readLine();
                    if (statusLine != null && statusLine.contains(" 200 ")) {
                        // Skip headers
                        String line;
                        while ((line = reader.readLine()) != null && !line.isEmpty()) {}
                        // Read body
                        StringBuilder body = new StringBuilder();
                        char[] buf = new char[512];
                        int n;
                        while ((n = reader.read(buf)) != -1) body.append(buf, 0, n);
                        String b = body.toString();
                        if (b.contains("\"healthy\"") || b.contains("\"degraded\"")) {
                            LOG.info("ORB healthy after " +
                                     (System.currentTimeMillis() - (deadline - timeout.toMillis())) + "ms");
                            return;
                        }
                    }
                }
            } catch (Exception e) {
                lastErr = e;
                // not ready yet
            }
        }

        throw new IllegalStateException(
                "ORB did not become healthy within " + timeout +
                (lastErr != null ? ": " + lastErr.getMessage() : ""));
    }

    private static void deleteDirectory(File dir) {
        if (dir == null || !dir.exists()) return;
        File[] files = dir.listFiles();
        if (files != null) {
            for (File f : files) {
                if (f.isDirectory()) deleteDirectory(f);
                else f.delete();
            }
        }
        dir.delete();
    }
}
