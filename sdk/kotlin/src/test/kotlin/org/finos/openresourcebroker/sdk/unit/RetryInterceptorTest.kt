package org.finos.openresourcebroker.sdk.unit

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.finos.openresourcebroker.sdk.transport.RetryConfig
import org.finos.openresourcebroker.sdk.transport.RetryInterceptor
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*

class RetryInterceptorTest {

    private fun makeClient(maxRetries: Int = 2, baseDelayMs: Long = 5L): Pair<MockWebServer, OkHttpClient> {
        val server = MockWebServer()
        server.start()
        val client = OkHttpClient.Builder()
            .addInterceptor(RetryInterceptor(RetryConfig(maxRetries = maxRetries, baseDelayMs = baseDelayMs)))
            .build()
        return server to client
    }

    @Test
    fun `GET succeeds on first attempt`() {
        val (server, client) = makeClient()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok"}"""))
            val resp = client.newCall(Request.Builder().url(server.url("/health")).get().build()).execute()
            assertEquals(200, resp.code)
            assertEquals(1, server.requestCount)
        } finally { server.shutdown() }
    }

    @Test
    fun `GET retries on 503 then succeeds`() {
        val (server, client) = makeClient()
        try {
            server.enqueue(MockResponse().setResponseCode(503))
            server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))
            val resp = client.newCall(Request.Builder().url(server.url("/test")).get().build()).execute()
            assertEquals(200, resp.code)
            assertEquals(2, server.requestCount)
        } finally { server.shutdown() }
    }

    @Test
    fun `GET retries on 429 then succeeds`() {
        val (server, client) = makeClient()
        try {
            server.enqueue(MockResponse().setResponseCode(429))
            server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))
            val resp = client.newCall(Request.Builder().url(server.url("/test")).get().build()).execute()
            assertEquals(200, resp.code)
            assertEquals(2, server.requestCount)
        } finally { server.shutdown() }
    }

    @Test
    fun `POST does NOT retry on 500`() {
        val (server, client) = makeClient()
        try {
            server.enqueue(MockResponse().setResponseCode(500).setBody("error"))
            server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))
            val body = "{}".toRequestBody("application/json".toMediaType())
            val resp = client.newCall(Request.Builder().url(server.url("/test")).post(body).build()).execute()
            assertEquals(500, resp.code)
            assertEquals(1, server.requestCount)
        } finally { server.shutdown() }
    }

    @Test
    fun `POST does NOT retry on 503 (non-idempotent — avoid double-provision)`() {
        val (server, client) = makeClient()
        try {
            server.enqueue(MockResponse().setResponseCode(503))
            server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))
            val body = "{}".toRequestBody("application/json".toMediaType())
            val resp = client.newCall(Request.Builder().url(server.url("/test")).post(body).build()).execute()
            // A provisioning POST may already have reached the server before the 503,
            // so it is NEVER auto-retried — the 503 is surfaced to the caller.
            assertEquals(503, resp.code)
            assertEquals(1, server.requestCount)
        } finally { server.shutdown() }
    }

    @Test
    fun `POST does NOT retry on 429`() {
        val (server, client) = makeClient()
        try {
            server.enqueue(MockResponse().setResponseCode(429))
            server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))
            val body = "{}".toRequestBody("application/json".toMediaType())
            val resp = client.newCall(Request.Builder().url(server.url("/test")).post(body).build()).execute()
            assertEquals(429, resp.code)
            assertEquals(1, server.requestCount)
        } finally { server.shutdown() }
    }

    @Test
    fun `PUT retries on 503 (idempotent)`() {
        val (server, client) = makeClient()
        try {
            server.enqueue(MockResponse().setResponseCode(503))
            server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))
            val body = "{}".toRequestBody("application/json".toMediaType())
            val resp = client.newCall(Request.Builder().url(server.url("/test")).put(body).build()).execute()
            assertEquals(200, resp.code)
            assertEquals(2, server.requestCount)
        } finally { server.shutdown() }
    }

    @Test
    fun `zero base delay does not throw (jitter guard)`() {
        val server = MockWebServer()
        server.start()
        try {
            val client = OkHttpClient.Builder()
                .addInterceptor(RetryInterceptor(RetryConfig(maxRetries = 2, baseDelayMs = 0L)))
                .build()
            server.enqueue(MockResponse().setResponseCode(503))
            server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))
            // baseDelayMs=0 collapses the jitter bound; the old ±50% additive jitter
            // (Random.nextLong(0,0)) threw IllegalArgumentException. Must not throw.
            val resp = client.newCall(Request.Builder().url(server.url("/test")).get().build()).execute()
            assertEquals(200, resp.code)
            assertEquals(2, server.requestCount)
        } finally { server.shutdown() }
    }

    @Test
    fun `4xx is not retried`() {
        val (server, client) = makeClient()
        try {
            server.enqueue(MockResponse().setResponseCode(404).setBody("not found"))
            val resp = client.newCall(Request.Builder().url(server.url("/missing")).get().build()).execute()
            assertEquals(404, resp.code)
            assertEquals(1, server.requestCount)
        } finally { server.shutdown() }
    }
}
