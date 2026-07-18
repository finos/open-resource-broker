package org.finos.openresourcebroker.sdk.unit

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.finos.openresourcebroker.sdk.auth.AuthOption
import org.finos.openresourcebroker.sdk.client.ClientConfig
import org.finos.openresourcebroker.sdk.client.OrbClient
import org.finos.openresourcebroker.sdk.client.OrbApiError
import org.finos.openresourcebroker.sdk.client.OrbConflictError
import org.finos.openresourcebroker.sdk.client.OrbNotFoundError
import org.finos.openresourcebroker.sdk.client.OrbUnavailableError
import org.finos.openresourcebroker.sdk.client.Scheduler
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*

/**
 * Unit tests for OrbClient against MockWebServer.
 * Each test creates its own server + client to avoid request ordering issues.
 */
class OrbClientMockTest {

    private suspend fun makeClient(server: MockWebServer): OrbClient {
        return OrbClient.create(
            ClientConfig(
                baseUrl = server.url("/").toString().trimEnd('/'),
                timeoutMs = 5_000L,
            )
        )
    }

    @Test
    fun `health - GET health returns status`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"healthy"}"""))
            val client = makeClient(server)
            val result = client.health()
            assertEquals("healthy", result["status"])
            assertEquals("/health", server.takeRequest().path)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `info - GET info`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"version":"1.0.0"}"""))
            val client = makeClient(server)
            val result = client.info()
            assertEquals("1.0.0", result["version"])
            assertEquals("/info", server.takeRequest().path)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `metrics - GET metrics returns text`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("# HELP orb_requests_total\norb_requests_total 42"))
            val client = makeClient(server)
            val result = client.metrics()
            assertTrue(result.contains("HELP"))
            assertEquals("/metrics", server.takeRequest().path)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `listTemplates - GET api v1 templates`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"templates":[]}"""))
            val client = makeClient(server)
            val result = client.listTemplates()
            assertNotNull(result)
            assertTrue(server.takeRequest().path!!.startsWith("/api/v1/templates/"))
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `getTemplate - 404 throws OrbApiError`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not found"}"""))
            val client = makeClient(server)
            val err = assertThrows<OrbApiError> { client.getTemplate("nonexistent") }
            assertEquals(404, err.statusCode)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `listMachines - GET api v1 machines`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"machines":[]}"""))
            val client = makeClient(server)
            val result = client.listMachines()
            assertNotNull(result)
            assertTrue(server.takeRequest().path!!.startsWith("/api/v1/machines/"))
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `getMachine - nonexistent returns 404`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not found"}"""))
            val client = makeClient(server)
            val err = assertThrows<OrbApiError> { client.getMachine("no-such-machine") }
            assertEquals(404, err.statusCode)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `listRequests - GET api v1 requests`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"requests":[]}"""))
            val client = makeClient(server)
            val result = client.listRequests()
            assertNotNull(result.requests)
            assertTrue(server.takeRequest().path!!.startsWith("/api/v1/requests/"))
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `cancelRequest - DELETE 404 for nonexistent request`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not found"}"""))
            val client = makeClient(server)
            val err = assertThrows<OrbApiError> { client.cancelRequest("no-such-id") }
            assertEquals(404, err.statusCode)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `500 response on POST throws OrbApiError (POST not retried on 500)`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            // POST to /api/v1/config/validate — POST 500 is NOT retried (non-idempotent)
            server.enqueue(MockResponse().setResponseCode(500).setBody("internal error"))
            val client = makeClient(server)
            // validateConfig() calls POST /api/v1/config/validate
            val err = assertThrows<OrbApiError> { client.validateConfig() }
            assertEquals(500, err.statusCode)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `401 throws OrbApiError that isUnauthorized`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(401).setBody("unauthorized"))
            val client = makeClient(server)
            val err = assertThrows<OrbApiError> { client.getMe() }
            assertTrue(err.isUnauthorized)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `scheduler header is NOT set for default scheduler`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"templates":[]}"""))
            val client = makeClient(server)
            client.listTemplates()
            val req = server.takeRequest()
            assertNull(req.getHeader("X-ORB-Scheduler"))
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `Bearer auth adds Authorization header`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"healthy"}"""))
            val client = OrbClient.create(
                ClientConfig(
                    baseUrl = server.url("/").toString().trimEnd('/'),
                    auth = AuthOption.Bearer("test-token-xyz"),
                    timeoutMs = 5_000L,
                )
            )
            client.health()
            val req = server.takeRequest()
            assertEquals("Bearer test-token-xyz", req.getHeader("Authorization"))
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `scheduler header hostfactory is set when configured`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"templates":[]}"""))
            val client = OrbClient.create(
                ClientConfig(
                    baseUrl = server.url("/").toString().trimEnd('/'),
                    scheduler = Scheduler.HOSTFACTORY,
                    timeoutMs = 5_000L,
                )
            )
            client.listTemplates()
            val req = server.takeRequest()
            assertEquals("hostfactory", req.getHeader("X-ORB-Scheduler"))
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `404 throws typed OrbNotFoundError sentinel`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Template not found"}"""))
            val client = makeClient(server)
            val err = assertThrows<OrbNotFoundError> { client.getTemplate("nope") }
            assertEquals(404, err.statusCode)
            assertTrue(err.isNotFound)
            assertEquals("Template not found", err.message?.substringAfter(": "))
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `error carries machine-readable code and request id`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(
                MockResponse()
                    .setResponseCode(409)
                    .setHeader("X-Request-ID", "req-abc-123")
                    .setBody("""{"error":{"code":"CONFLICT","message":"already exists"}}""")
            )
            val client = makeClient(server)
            val err = assertThrows<OrbConflictError> { client.getMe() }
            assertEquals(409, err.statusCode)
            assertEquals("CONFLICT", err.code)
            assertEquals("req-abc-123", err.requestId)
            assertTrue(err.isConflict)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `503 sentinel is OrbUnavailableError`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            // POST is not retried on 503, so validateConfig surfaces the typed error.
            server.enqueue(MockResponse().setResponseCode(503).setBody("""{"detail":"unavailable"}"""))
            val client = makeClient(server)
            val err = assertThrows<OrbUnavailableError> { client.validateConfig() }
            assertTrue(err.isUnavailable)
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `health returns parsed body on 503 (data not error)`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(503).setBody("""{"status":"unhealthy"}"""))
            val client = makeClient(server)
            val result = client.health()
            assertEquals("unhealthy", result["status"])
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `path segment with a space is percent-encoded not plus-encoded`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"id":"x"}"""))
            val client = makeClient(server)
            client.getMachine("foo bar")
            val req = server.takeRequest()
            // A path segment must use %20 for a space, never '+', or it addresses
            // the wrong resource.
            assertTrue(req.path!!.contains("foo%20bar"), "expected %20, got ${req.path}")
            assertFalse(req.path!!.contains("foo+bar"), "must not use '+' in a path: ${req.path}")
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `Bearer token provider is invoked on every request (refresh)`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"healthy"}"""))
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"healthy"}"""))
            var counter = 0
            val client = OrbClient.create(
                ClientConfig(
                    baseUrl = server.url("/").toString().trimEnd('/'),
                    auth = AuthOption.Bearer.of { "token-${++counter}" },
                    timeoutMs = 5_000L,
                )
            )
            client.health()
            client.health()
            assertEquals("Bearer token-1", server.takeRequest().getHeader("Authorization"))
            assertEquals("Bearer token-2", server.takeRequest().getHeader("Authorization"))
            client.close()
        } finally { server.shutdown() }
    }

    @Test
    fun `cancelRequest sends reason query param`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
            val client = makeClient(server)
            client.cancelRequest("req-1", reason = "no longer needed")
            val req = server.takeRequest()
            assertTrue(req.path!!.contains("reason=no%20longer%20needed"), "path: ${req.path}")
            client.close()
        } finally { server.shutdown() }
    }
}
