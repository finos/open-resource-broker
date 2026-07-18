/**
 * ORB SDK error hierarchy.
 *
 * Cross-SDK contract (shared with Go/TypeScript/Java/C#):
 *   - [OrbError] is the base type for everything the SDK throws.
 *   - [OrbApiError] is thrown for all HTTP error responses and carries the
 *     canonical field set: statusCode (httpStatus), code (machine-readable
 *     errorCode, may be null), message, requestId (for support correlation),
 *     plus the raw response body.
 *   - Typed sentinel subclasses exist for 401/403/404/409/503/408 so callers
 *     can `catch (e: OrbNotFoundError)` or branch with `is OrbNotFoundError`.
 *
 * All errors are declared here in sdk.client so callers only need to import
 * from a single, stable package. The sse package imports these types for use
 * inside the reconnecting SSE stream.
 */
package org.finos.openresourcebroker.sdk.client

/**
 * Base exception for all ORB client errors.
 *
 * Extends [RuntimeException] (matching the Java/C# SDKs) so it can be thrown
 * from OkHttp interceptors and other unchecked contexts, and so callers are
 * never forced to declare it.
 */
open class OrbError(message: String, cause: Throwable? = null) : RuntimeException(message, cause)

/**
 * An HTTP error response from the ORB API.
 *
 * Carries the canonical cross-SDK field set: HTTP [statusCode], machine-readable
 * [code] (may be null), message, server-assigned [requestId], and the raw
 * response [body].
 *
 * @property statusCode HTTP status code (e.g. 404, 500)
 * @property code Machine-readable error code from the body, if present
 * @property requestId Server-assigned request ID (X-Request-ID) for correlation
 * @property body Raw response body, if available
 */
open class OrbApiError(
    val statusCode: Int,
    message: String,
    val code: String? = null,
    val requestId: String? = null,
    val body: String? = null,
    cause: Throwable? = null,
) : OrbError(formatMessage(statusCode, code, message), cause) {

    /** True if the error is a 404 at the resource level (resource not found). */
    val isNotFound: Boolean get() = statusCode == 404

    /** True if the error is a 401 Unauthorized. */
    val isUnauthorized: Boolean get() = statusCode == 401

    /** True if the error is a 403 Forbidden. */
    val isForbidden: Boolean get() = statusCode == 403

    /** True if the error is a 409 Conflict. */
    val isConflict: Boolean get() = statusCode == 409

    /** True if the error is a 503 Service Unavailable. */
    val isUnavailable: Boolean get() = statusCode == 503

    /** True if the error is a 408 Request Timeout. */
    val isTimeout: Boolean get() = statusCode == 408

    /** True if the error is a 422 validation error. */
    val isValidationError: Boolean get() = statusCode == 422

    /** True if the error is any 5xx server error. */
    val isServerError: Boolean get() = statusCode >= 500

    companion object {
        private fun formatMessage(statusCode: Int, code: String?, message: String): String =
            if (!code.isNullOrEmpty()) "HTTP $statusCode [$code]: $message"
            else "HTTP $statusCode: $message"

        /**
         * Construct the most specific [OrbApiError] subclass for an HTTP status so
         * `is OrbNotFoundError` (etc.) works for callers, falling back to the base
         * [OrbApiError] for statuses without a typed sentinel. Mirrors TS
         * `apiErrorForStatus`, Java `OrbApiException.forStatus` and Go
         * `sentinelForStatus`.
         */
        fun forStatus(
            statusCode: Int,
            message: String,
            code: String? = null,
            requestId: String? = null,
            body: String? = null,
        ): OrbApiError = when (statusCode) {
            401 -> OrbUnauthorizedError(message, code, requestId, body)
            403 -> OrbForbiddenError(message, code, requestId, body)
            404 -> OrbNotFoundError(message, code, requestId, body)
            409 -> OrbConflictError(message, code, requestId, body)
            408 -> OrbTimeoutError(message, code, requestId, body)
            503 -> OrbUnavailableError(message, code, requestId, body)
            else -> OrbApiError(statusCode, message, code, requestId, body)
        }
    }
}

/** Typed sentinel for HTTP 401 (unauthorized). */
class OrbUnauthorizedError(
    message: String = "orb: unauthorized",
    code: String? = null,
    requestId: String? = null,
    body: String? = null,
) : OrbApiError(401, message, code, requestId, body)

/** Typed sentinel for HTTP 403 (forbidden). */
class OrbForbiddenError(
    message: String = "orb: forbidden",
    code: String? = null,
    requestId: String? = null,
    body: String? = null,
) : OrbApiError(403, message, code, requestId, body)

/** Typed sentinel for HTTP 404 (resource not found). */
class OrbNotFoundError(
    message: String = "orb: not found",
    code: String? = null,
    requestId: String? = null,
    body: String? = null,
) : OrbApiError(404, message, code, requestId, body)

/** Typed sentinel for HTTP 409 (conflict). */
class OrbConflictError(
    message: String = "orb: conflict",
    code: String? = null,
    requestId: String? = null,
    body: String? = null,
) : OrbApiError(409, message, code, requestId, body)

/** Typed sentinel for HTTP 408 (request timeout). */
class OrbTimeoutError(
    message: String = "orb: request timeout",
    code: String? = null,
    requestId: String? = null,
    body: String? = null,
) : OrbApiError(408, message, code, requestId, body)

/**
 * Typed sentinel for HTTP 503 (service unavailable).
 *
 * Also used (via the message-only constructor) to signal that a managed ORB
 * subprocess is unhealthy, mirroring the Java `OrbUnavailableException`.
 */
class OrbUnavailableError(
    message: String = "orb: service unavailable",
    code: String? = null,
    requestId: String? = null,
    body: String? = null,
) : OrbApiError(503, message, code, requestId, body)
