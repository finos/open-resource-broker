/**
 * Layer 3: Retry Interceptor with Exponential Back-off
 *
 * Retries only transient failures on idempotent methods:
 *   - HTTP 429/503 and other 5xx — idempotent methods only (GET, HEAD, PUT, DELETE, OPTIONS)
 *   - Network errors (IOException) — idempotent methods only
 *
 * A non-idempotent POST is NEVER auto-retried on 429/503 or on a post-write
 * network error: a provisioning POST may already have reached the server before
 * the socket dropped, so a blind retry risks silently double-provisioning
 * machines. Only a pre-write connection failure (connection refused) is safe to
 * retry for POST, because the server never saw the request; OkHttp surfaces that
 * as a [java.net.ConnectException].
 *
 * Uses multiplicative jitter (0.5x–1.0x of the capped delay) which — unlike the
 * previous ±50% additive form — never throws for small base delays.
 */

package org.finos.openresourcebroker.sdk.transport

import okhttp3.Interceptor
import okhttp3.Response
import java.io.IOException
import java.net.ConnectException
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

private val IDEMPOTENT_METHODS = setOf("GET", "HEAD", "PUT", "DELETE", "OPTIONS")

/**
 * Configuration for retry behaviour.
 */
data class RetryConfig(
    val maxRetries: Int = 3,
    val baseDelayMs: Long = 500L,
    val maxDelayMs: Long = 30_000L,
)

class RetryInterceptor(private val cfg: RetryConfig = RetryConfig()) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        var attempt = 0
        var lastException: IOException? = null

        while (attempt <= cfg.maxRetries) {
            if (attempt > 0) {
                Thread.sleep(backoffMs(attempt - 1))
            }

            val request = chain.request()
            val idempotent = request.method.uppercase() in IDEMPOTENT_METHODS

            val response: Response? = try {
                chain.proceed(request)
            } catch (e: ConnectException) {
                // Connection refused before any bytes were written: the server never
                // saw the request, so it is safe to retry even for a POST.
                lastException = e
                attempt++
                continue
            } catch (e: IOException) {
                lastException = e
                if (!idempotent) {
                    // Non-idempotent (POST): a post-write network error may mean the
                    // request was already processed. Never blind-retry — fail loud.
                    throw e
                }
                null
            }

            // Network error on an idempotent method — retry
            if (response == null) {
                attempt++
                continue
            }

            // Determine if we should retry this response
            if (!shouldRetry(request.method, response.code)) {
                return response
            }

            response.close()
            attempt++
        }

        lastException?.let { throw it }
        throw IOException("RetryInterceptor: exceeded ${cfg.maxRetries} retries")
    }

    /**
     * Retry policy for HTTP status codes. 429/503 and other 5xx are retried ONLY
     * for idempotent methods; a POST is never retried on these because the server
     * may have processed it before failing to respond. 4xx (except 429) is never
     * retried.
     */
    private fun shouldRetry(method: String, status: Int): Boolean {
        if (method.uppercase() !in IDEMPOTENT_METHODS) return false
        if (status == 429 || status == 503) return true
        return status in 500..599
    }

    private fun backoffMs(attempt: Int): Long {
        val raw = cfg.baseDelayMs * 2.0.pow(attempt.toDouble())
        val capped = min(raw.toLong(), cfg.maxDelayMs).coerceAtLeast(0L)
        // Multiplicative jitter in [0.5, 1.0) of the capped delay. Never throws,
        // even when capped is 0 or 1 (unlike Random.nextLong(-capped/2, capped/2)).
        return (capped * (0.5 + Random.nextDouble() * 0.5)).toLong()
    }
}
