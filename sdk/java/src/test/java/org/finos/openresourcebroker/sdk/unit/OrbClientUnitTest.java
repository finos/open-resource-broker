package org.finos.openresourcebroker.sdk.unit;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;

import org.finos.openresourcebroker.sdk.auth.AwsSigV4Auth;
import org.finos.openresourcebroker.sdk.auth.BearerTokenAuth;
import org.finos.openresourcebroker.sdk.client.OrbApiException;
import org.finos.openresourcebroker.sdk.client.OrbClient;
import org.finos.openresourcebroker.sdk.client.OrbNotFoundException;
import org.finos.openresourcebroker.sdk.client.OrbUnavailableException;
import org.finos.openresourcebroker.sdk.model.MachineItem;
import org.finos.openresourcebroker.sdk.model.TemplateItem;
import org.finos.openresourcebroker.sdk.model.TemplateListResponse;

import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.net.URI;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for OrbClient against a mock HTTP server.
 *
 * <p>These tests do NOT require a real ORB process — they use MockWebServer.
 */
class OrbClientUnitTest {

    private MockWebServer server;
    private OrbClient client;

    @BeforeEach
    void setUp() throws Exception {
        server = new MockWebServer();
        server.start();

        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .timeout(Duration.ofSeconds(5))
                .maxRetries(0) // disable retries in unit tests for speed
                .build();
    }

    @AfterEach
    void tearDown() throws Exception {
        client.close();
        server.shutdown();
    }

    // ──────────────────────────────────────────────────────────────────────
    // Health check
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testHealthCheck_returnsHealthyStatus() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"status\": \"healthy\"}"));

        Map<String, Object> result = client.health();
        assertEquals("healthy", result.get("status"),
                "health check should return 'healthy' status");

        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        assertNotNull(req);
        assertEquals("GET", req.getMethod());
        assertEquals("/health", req.getPath());
    }

    // ──────────────────────────────────────────────────────────────────────
    // List templates — happy path
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testListTemplates_emptyList() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": [], \"message\": null, \"success\": true}"));

        TemplateListResponse result = client.listTemplates(null, null, null);
        assertNotNull(result);
        assertNotNull(result.getTemplates());
        assertEquals(0, result.getTemplates().size());

        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        assertEquals("GET", req.getMethod());
        assertTrue(req.getPath().startsWith("/api/v1/templates/"));
    }

    @Test
    void testListTemplates_withFilters() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": [], \"total_count\": 0}"));

        client.listTemplates("aws", 10, 5);

        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        String path = req.getPath();
        assertTrue(path.contains("provider_api=aws"), "should include provider_api param: " + path);
        assertTrue(path.contains("limit=10"), "should include limit param: " + path);
        assertTrue(path.contains("offset=5"), "should include offset param: " + path);
    }

    // ──────────────────────────────────────────────────────────────────────
    // Error propagation
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testGetTemplate_404_throwsOrbApiException() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(404)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"detail\": \"Template not found\"}"));

        OrbApiException ex = assertThrows(OrbApiException.class,
                () -> client.getTemplate("nonexistent-template"));
        assertEquals(404, ex.getStatusCode());
        assertTrue(ex.isNotFound());
    }

    @Test
    void testGetTemplate_notFound_errorCode() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(404)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"error\": {\"code\": \"TEMPLATE_NOT_FOUND\", \"message\": \"Template not found\"}}"));

        OrbApiException ex = assertThrows(OrbApiException.class,
                () -> client.getTemplate("missing"));
        assertEquals(404, ex.getStatusCode());
        assertEquals("TEMPLATE_NOT_FOUND", ex.getCode());
    }

    @Test
    void testServerError_500_throwsOrbApiException() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(500)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"detail\": \"Internal server error\"}"));

        OrbApiException ex = assertThrows(OrbApiException.class,
                () -> client.listMachines(null, null, null, null, null));
        assertEquals(500, ex.getStatusCode());
        assertTrue(ex.isServerError());
    }

    @Test
    void testValidationError_422() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(422)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"detail\": [{\"loc\": [\"body\", \"templateId\"], \"msg\": \"field required\", \"type\": \"missing\"}]}"));

        OrbApiException ex = assertThrows(OrbApiException.class,
                () -> client.requestMachines(null));
        assertEquals(422, ex.getStatusCode());
        assertTrue(ex.isValidationError());
    }

    // ──────────────────────────────────────────────────────────────────────
    // Bearer token auth
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testBearerTokenAuth_sentOnEveryRequest() throws Exception {
        // Rebuild client with bearer auth
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .auth(new BearerTokenAuth("test-secret-token"))
                .timeout(Duration.ofSeconds(5))
                .maxRetries(0)
                .build();

        for (int i = 0; i < 3; i++) {
            server.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("{\"status\": \"healthy\"}"));
        }

        for (int i = 0; i < 3; i++) {
            client.health();
            RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
            assertNotNull(req, "request " + i + " not received");
            String auth = req.getHeader("Authorization");
            assertEquals("Bearer test-secret-token", auth,
                    "Authorization header missing or wrong on request " + i);
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Retry behaviour
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testRetry_503ThenSuccess_idempotentGet() throws Exception {
        // Rebuild client with retries enabled
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .timeout(Duration.ofSeconds(5))
                .maxRetries(2)
                .retryBaseDelay(Duration.ofMillis(10)) // fast for tests
                .build();

        // First 2 attempts → 503; 3rd → 200. A GET is idempotent, so it retries.
        server.enqueue(new MockResponse().setResponseCode(503).setBody("Service Unavailable"));
        server.enqueue(new MockResponse().setResponseCode(503).setBody("Service Unavailable"));
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": []}"));

        TemplateListResponse result = client.listTemplates();
        assertNotNull(result, "should succeed after 2 retries");
        assertEquals(3, server.getRequestCount(), "GET should retry 503 twice then succeed");
    }

    @Test
    void testRetry_429ThenSuccess_idempotentGet() throws Exception {
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .timeout(Duration.ofSeconds(5))
                .maxRetries(1)
                .retryBaseDelay(Duration.ofMillis(10))
                .build();

        server.enqueue(new MockResponse().setResponseCode(429).setBody("Too Many Requests"));
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": []}"));

        TemplateListResponse result = client.listTemplates();
        assertNotNull(result);
    }

    @Test
    void testRetry_postNot5xx_noRetry() throws Exception {
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .timeout(Duration.ofSeconds(5))
                .maxRetries(3)
                .retryBaseDelay(Duration.ofMillis(10))
                .build();

        // 500 on a POST — must NOT be retried (POST is non-idempotent)
        server.enqueue(new MockResponse().setResponseCode(500).setBody("{\"detail\": \"error\"}"));

        assertThrows(OrbApiException.class,
                () -> client.requestMachines(null));

        // Only 1 request should have been made (no retry)
        assertEquals(1, server.getRequestCount(),
                "POST on 500 should not be retried");
    }

    @Test
    void testRetry_postNotRetriedOn503() throws Exception {
        // A non-idempotent POST must NEVER be retried on 503: the server may have
        // already processed it before failing, so a blind retry risks duplicate
        // provisioning. This is the core cross-SDK safety invariant.
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .timeout(Duration.ofSeconds(5))
                .maxRetries(3)
                .retryBaseDelay(Duration.ofMillis(10))
                .build();

        server.enqueue(new MockResponse().setResponseCode(503).setBody("{\"detail\": \"unavailable\"}"));

        var body = new org.finos.openresourcebroker.sdk.model.RequestMachinesRequest();
        body.setTemplateId("tmpl-1");
        body.setCount(1);

        OrbApiException ex = assertThrows(OrbApiException.class,
                () -> client.requestMachines(body));
        assertEquals(503, ex.getStatusCode());
        assertEquals(1, server.getRequestCount(),
                "POST on 503 must NOT be retried (duplicate-provisioning risk)");
    }

    @Test
    void testRetry_putRetriedOn503_idempotent() throws Exception {
        // PUT is idempotent, so retrying on 503 is safe and expected.
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .timeout(Duration.ofSeconds(5))
                .maxRetries(2)
                .retryBaseDelay(Duration.ofMillis(10))
                .build();

        server.enqueue(new MockResponse().setResponseCode(503).setBody("unavailable"));
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"success\": true}"));

        var req = new org.finos.openresourcebroker.sdk.model.TemplateUpdateRequest();
        req.setName("x");
        client.updateTemplate("tmpl-1", req);
        assertEquals(2, server.getRequestCount(), "PUT should retry 503 then succeed");
    }

    // ──────────────────────────────────────────────────────────────────────
    // Scheduler header
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testSchedulerHeader_hostfactory() throws Exception {
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .scheduler("hostfactory")
                .timeout(Duration.ofSeconds(5))
                .maxRetries(0)
                .build();

        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": []}"));

        client.listTemplates(null, null, null);

        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        assertEquals("hostfactory", req.getHeader("X-ORB-Scheduler"),
                "should send X-ORB-Scheduler header for hostfactory scheduler");
    }

    @Test
    void testSchedulerHeader_default_notSent() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": []}"));

        client.listTemplates(null, null, null);

        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        assertNull(req.getHeader("X-ORB-Scheduler"),
                "should NOT send X-ORB-Scheduler for default scheduler");
    }

    // ──────────────────────────────────────────────────────────────────────
    // Healthy returns true without managed process
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testHealthy_trueWithoutManagedProcess() throws Exception {
        assertTrue(client.isHealthy(),
                "isHealthy() should return true when not in managed-process mode");
    }

    // ──────────────────────────────────────────────────────────────────────
    // Request machines — POST /api/v1/machines/request
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testRequestMachines_returns202() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(202)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"request_id\": \"req-abc123\", \"message\": \"accepted\"}"));

        var body = new org.finos.openresourcebroker.sdk.model.RequestMachinesRequest();
        body.setTemplateId("tmpl-1");
        body.setCount(2);

        var result = client.requestMachines(body);
        assertNotNull(result);
        assertEquals("req-abc123", result.getRequestId());

        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        assertEquals("POST", req.getMethod());
        assertEquals("/api/v1/machines/request", req.getPath());
    }

    // ──────────────────────────────────────────────────────────────────────
    // Return machines — POST /api/v1/machines/return
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testReturnMachines_sendsCorrectPayload() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"request_id\": \"ret-123\", \"message\": \"ok\"}"));

        var req = new org.finos.openresourcebroker.sdk.model.ReturnMachinesRequest();
        req.setMachineIds(java.util.List.of("i-111", "i-222"));
        client.returnMachines(req);

        RecordedRequest recorded = server.takeRequest(1, TimeUnit.SECONDS);
        assertEquals("POST", recorded.getMethod());
        assertTrue(recorded.getPath().equals("/api/v1/machines/return"));
        String bodyStr = recorded.getBody().readUtf8();
        assertTrue(bodyStr.contains("machineIds") || bodyStr.contains("machine_ids"),
                "body should contain machineIds: " + bodyStr);
    }

    // ──────────────────────────────────────────────────────────────────────
    // Cancel request — DELETE /api/v1/requests/{id}
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testCancelRequest_sendsDelete() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"request_id\": \"req-xyz\", \"message\": \"cancelled\"}"));

        client.cancelRequest("req-xyz", null);

        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        assertEquals("DELETE", req.getMethod());
        assertTrue(req.getPath().startsWith("/api/v1/requests/req-xyz"));
    }

    // ──────────────────────────────────────────────────────────────────────
    // Metrics — GET /metrics returns plain text
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testMetrics_returnsText() throws Exception {
        String prometheusText = "# HELP process_cpu_seconds_total\n# TYPE process_cpu_seconds_total counter\nprocess_cpu_seconds_total 0.1\n";
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "text/plain; version=0.0.4")
                .setBody(prometheusText));

        String result = client.metrics();
        assertNotNull(result);
        assertTrue(result.contains("process_cpu"), "should return prometheus text: " + result);
    }

    // ──────────────────────────────────────────────────────────────────────
    // SigV4 — signRequest() produces a real Authorization header (not a marker)
    // ──────────────────────────────────────────────────────────────────────

    /**
     * Asserts that {@link AwsSigV4Auth#signRequest} produces a real
     * {@code Authorization: AWS4-HMAC-SHA256 ...} header when called with static
     * credentials.  This verifies that the non-deprecated {@code AwsV4HttpSigner}
     * is wired correctly and that the old marker-header hack is gone.
     */
    @Test
    void testSigV4Auth_signRequest_producesRealAuthorizationHeader() throws Exception {
        AwsSigV4Auth sigv4 = new AwsSigV4Auth(
                "AKIATESTEXAMPLEKEYID",
                "wJalrXUtnFEMI/K7MDENG/bPxRfiCYTESTKEY",
                null,          // no session token
                "us-east-1",
                "execute-api"
        );

        URI uri = URI.create("http://localhost:8000/api/v1/templates/");
        Map<String, String> headers = sigv4.signRequest("GET", uri, new byte[0]);

        // Must contain a real Authorization header
        String auth = headers.entrySet().stream()
                .filter(e -> e.getKey().equalsIgnoreCase("authorization"))
                .map(Map.Entry::getValue)
                .findFirst()
                .orElse(null);

        assertNotNull(auth, "signRequest must produce an Authorization header");
        assertTrue(auth.startsWith("AWS4-HMAC-SHA256 "),
                "Authorization must use AWS4-HMAC-SHA256 signature, got: " + auth);
        assertTrue(auth.contains("Credential="),  "must contain Credential: " + auth);
        assertTrue(auth.contains("SignedHeaders="), "must contain SignedHeaders: " + auth);
        assertTrue(auth.contains("Signature="),   "must contain Signature: " + auth);

        // Must NOT be the old marker header
        assertFalse(headers.containsKey("X-Orb-Auth-Strategy"),
                "signRequest must not produce the old X-Orb-Auth-Strategy marker header");

        // Must contain x-amz-date
        String amzDate = headers.entrySet().stream()
                .filter(e -> e.getKey().equalsIgnoreCase("x-amz-date"))
                .map(Map.Entry::getValue)
                .findFirst()
                .orElse(null);
        assertNotNull(amzDate, "signRequest must produce an x-amz-date header");
        // x-amz-date format: yyyyMMdd'T'HHmmss'Z'
        assertTrue(amzDate.matches("\\d{8}T\\d{6}Z"),
                "x-amz-date must match yyyyMMddTHHmmssZ, got: " + amzDate);

        // NOTE: never print the Authorization header or any x-amz-* value — the
        // signature (and, with session credentials, x-amz-security-token) is secret
        // material that would leak into CI logs.  Assert on presence/format only.
    }

    /**
     * Asserts that when {@link OrbClient} is built with {@link AwsSigV4Auth}, the
     * first request carries a real {@code Authorization: AWS4-HMAC-SHA256} header
     * on the wire — NOT the old {@code X-Orb-Auth-Strategy: sigv4} marker.
     */
    @Test
    void testSigV4WiredIntoClient_sendsRealAuthorizationHeader() throws Exception {
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .auth(new AwsSigV4Auth(
                        "AKIATESTEXAMPLEKEYID",
                        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYTESTKEY",
                        null,
                        "us-east-1",
                        "execute-api"))
                .timeout(Duration.ofSeconds(5))
                .maxRetries(0)
                .build();

        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"status\": \"healthy\"}"));

        client.health();

        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        assertNotNull(req, "request was not received by mock server");

        String auth = req.getHeader("Authorization");
        assertNotNull(auth, "Authorization header must be present on SigV4-signed request");
        assertTrue(auth.startsWith("AWS4-HMAC-SHA256 "),
                "Authorization must use AWS4-HMAC-SHA256, got: " + auth);

        // The old marker header must NOT be present
        assertNull(req.getHeader("X-Orb-Auth-Strategy"),
                "X-Orb-Auth-Strategy marker header must not be sent; SigV4 must use real Authorization");

        // x-amz-date must also be present
        String amzDate = req.getHeader("x-amz-date");
        assertNotNull(amzDate, "x-amz-date header must be present on SigV4-signed request");

        // Do NOT print Authorization / x-amz-* — secret material must not hit CI logs.
    }

    // ──────────────────────────────────────────────────────────────────────
    // health() returns the body on 503 (degraded), never throws
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testHealth_503_returnsDegradedBody() throws Exception {
        // A 503 health response carries a valid degraded/unhealthy body and must
        // be RETURNED, not thrown — matching Go/TS/Kotlin/C#.
        server.enqueue(new MockResponse()
                .setResponseCode(503)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"status\": \"unhealthy\"}"));

        Map<String, Object> result = client.health();
        assertNotNull(result, "health() must return the body on 503, not throw");
        assertEquals("unhealthy", result.get("status"));
    }

    @Test
    void testHealth_503_notRetryLooped() throws Exception {
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .timeout(Duration.ofSeconds(5))
                .maxRetries(3)
                .retryBaseDelay(Duration.ofMillis(10))
                .build();

        server.enqueue(new MockResponse()
                .setResponseCode(503)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"status\": \"degraded\"}"));

        Map<String, Object> result = client.health();
        assertEquals("degraded", result.get("status"));
        assertEquals(1, server.getRequestCount(),
                "health() must not retry-loop on 503");
    }

    // ──────────────────────────────────────────────────────────────────────
    // Typed error sentinels + request ID
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testNotFound_typedSentinel() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(404)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"detail\": \"Template not found\"}"));

        // The base catch works AND the typed subclass instanceof works.
        OrbApiException ex = assertThrows(OrbNotFoundException.class,
                () -> client.getTemplate("missing"));
        assertTrue(ex instanceof OrbNotFoundException);
        assertTrue(ex.isNotFound());
    }

    @Test
    void testUnavailable_typedSentinel() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(503)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"detail\": \"unavailable\"}"));

        assertThrows(OrbUnavailableException.class,
                () -> client.listMachines());
    }

    @Test
    void testError_carriesRequestId() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(500)
                .setHeader("Content-Type", "application/json")
                .setHeader("X-Request-ID", "req-correlation-42")
                .setBody("{\"detail\": \"boom\"}"));

        OrbApiException ex = assertThrows(OrbApiException.class,
                () -> client.listMachines());
        assertEquals("req-correlation-42", ex.getRequestId(),
                "OrbApiException must carry the server request ID for correlation");
    }

    // ──────────────────────────────────────────────────────────────────────
    // Single getters unwrap to a single item
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testGetTemplate_unwrapsSingleItem() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": [{\"template_id\": \"tmpl-9\", \"name\": \"n\"}]}"));

        TemplateItem item = client.getTemplate("tmpl-9");
        assertNotNull(item, "getTemplate should return the single item, not a list envelope");
        assertEquals("tmpl-9", item.getTemplateId());
    }

    @Test
    void testGetMachine_unwrapsSingleItem() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"machines\": [{\"machine_id\": \"i-123\", \"name\": \"m\"}]}"));

        MachineItem item = client.getMachine("i-123");
        assertNotNull(item, "getMachine should return the single item, not a list envelope");
        assertEquals("i-123", item.getMachineId());
    }

    // ──────────────────────────────────────────────────────────────────────
    // Bearer token refresh: supplier invoked on EVERY request
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testBearerToken_refreshedPerRequest() throws Exception {
        client.close();
        AtomicInteger counter = new AtomicInteger(0);
        // Supplier returns a different token each call — a stale-capture bug would
        // send "token-1" forever.
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .auth(new BearerTokenAuth(() -> "token-" + counter.incrementAndGet()))
                .timeout(Duration.ofSeconds(5))
                .maxRetries(0)
                .build();

        for (int i = 0; i < 3; i++) {
            server.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("{\"templates\": []}"));
        }

        for (int i = 1; i <= 3; i++) {
            client.listTemplates();
            RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
            assertNotNull(req);
            assertEquals("Bearer token-" + i, req.getHeader("Authorization"),
                    "Bearer supplier must be invoked on every request (token refresh)");
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Scheduler enum
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testScheduler_enumHostFactory() throws Exception {
        client.close();
        client = OrbClient.builder()
                .baseUrl("http://localhost:" + server.getPort())
                .scheduler(org.finos.openresourcebroker.sdk.client.Scheduler.HOSTFACTORY)
                .timeout(Duration.ofSeconds(5))
                .maxRetries(0)
                .build();

        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": []}"));

        client.listTemplates();
        RecordedRequest req = server.takeRequest(1, TimeUnit.SECONDS);
        assertEquals("hostfactory", req.getHeader("X-ORB-Scheduler"));
    }

    @Test
    void testScheduler_rejectsUnknownWireValue() {
        assertThrows(IllegalArgumentException.class,
                () -> OrbClient.builder().scheduler("bogus-scheduler"));
    }

    // ──────────────────────────────────────────────────────────────────────
    // TLS: https:// must attempt a TLS handshake, never plaintext
    // ──────────────────────────────────────────────────────────────────────

    @Test
    void testHttps_attemptsTlsHandshake_notPlaintext() throws Exception {
        // Point an https:// client at a PLAINTEXT MockWebServer. If the transport
        // downgraded to plaintext (the old bug) the request would succeed and the
        // Bearer token would go out in cleartext. With the TLS fix the client
        // performs a TLS handshake that the plaintext server cannot complete, so
        // the call fails — proving the token is never sent unencrypted.
        client.close();
        client = OrbClient.builder()
                .baseUrl("https://localhost:" + server.getPort())
                .auth(new BearerTokenAuth("super-secret-token"))
                .timeout(Duration.ofSeconds(3))
                .maxRetries(0)
                .build();

        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"templates\": []}"));

        // Must fail (handshake against plaintext), NOT silently send plaintext.
        assertThrows(Exception.class, () -> client.listTemplates(),
                "https:// must perform a TLS handshake, never fall back to plaintext");

        // The plaintext server must NOT have received a readable HTTP request with
        // the Bearer token in the clear.
        RecordedRequest leaked = server.takeRequest(500, TimeUnit.MILLISECONDS);
        if (leaked != null) {
            assertNull(leaked.getHeader("Authorization"),
                    "Bearer token must never be transmitted over a plaintext connection");
        }
    }
}
