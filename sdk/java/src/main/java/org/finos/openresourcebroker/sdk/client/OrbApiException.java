package org.finos.openresourcebroker.sdk.client;

/**
 * Exception thrown for API-level errors (HTTP 4xx/5xx responses from ORB).
 *
 * <p>Carries the canonical cross-SDK field set shared with Go/TypeScript/Kotlin/C#:
 * HTTP status ({@link #getStatusCode()}), machine-readable error code
 * ({@link #getCode()}, may be null), message, and the server-assigned request ID
 * ({@link #getRequestId()}) for support correlation.
 *
 * <p>Extends {@link OrbError} so callers can catch every SDK failure via a single
 * base type.  Typed sentinel subclasses (e.g. {@link OrbNotFoundException}) exist
 * for 401/403/404/409/503/408 so callers can branch with {@code instanceof}.
 */
public class OrbApiException extends OrbError {

    private final int statusCode;
    private final String code;
    private final String requestId;

    public OrbApiException(int statusCode, String code, String message, String requestId) {
        super(formatMessage(statusCode, code, message));
        this.statusCode = statusCode;
        this.code = code;
        this.requestId = requestId;
    }

    public OrbApiException(int statusCode, String code, String message) {
        this(statusCode, code, message, null);
    }

    public OrbApiException(int statusCode, String message) {
        this(statusCode, null, message, null);
    }

    private static String formatMessage(int statusCode, String code, String message) {
        if (code != null && !code.isEmpty()) {
            return "ORB HTTP " + statusCode + " [" + code + "]: " + message;
        }
        return "ORB HTTP " + statusCode + ": " + message;
    }

    /** HTTP status code (e.g. 404, 500). */
    public int getStatusCode() { return statusCode; }

    /** ORB error code string, or null if not present. */
    public String getCode() { return code; }

    /** Server-assigned request ID (X-Request-ID) for support/correlation, or null. */
    public String getRequestId() { return requestId; }

    /** Returns true if this is a "not found" error (HTTP 404). */
    public boolean isNotFound() { return statusCode == 404; }

    /** Returns true if this is an "unauthorized" error (HTTP 401). */
    public boolean isUnauthorized() { return statusCode == 401; }

    /** Returns true if this is a "forbidden" error (HTTP 403). */
    public boolean isForbidden() { return statusCode == 403; }

    /** Returns true if this is a "conflict" error (HTTP 409). */
    public boolean isConflict() { return statusCode == 409; }

    /** Returns true if this is a "service unavailable" error (HTTP 503). */
    public boolean isUnavailable() { return statusCode == 503; }

    /** Returns true if this is a "request timeout" error (HTTP 408). */
    public boolean isTimeout() { return statusCode == 408; }

    /** Returns true if this is a validation error (HTTP 422). */
    public boolean isValidationError() { return statusCode == 422; }

    /** Returns true if this is a server error (HTTP 5xx). */
    public boolean isServerError() { return statusCode >= 500; }

    /**
     * Construct the most specific {@link OrbApiException} subclass for an HTTP
     * status so {@code instanceof OrbNotFoundException} (etc.) works for callers,
     * falling back to the base {@link OrbApiException} for statuses without a
     * typed sentinel.  Mirrors TS {@code apiErrorForStatus} and Go
     * {@code sentinelForStatus}.
     */
    public static OrbApiException forStatus(int statusCode, String code, String message,
                                            String requestId) {
        return switch (statusCode) {
            case 401 -> new OrbUnauthorizedException(code, message, requestId);
            case 403 -> new OrbForbiddenException(code, message, requestId);
            case 404 -> new OrbNotFoundException(code, message, requestId);
            case 409 -> new OrbConflictException(code, message, requestId);
            case 408 -> new OrbTimeoutException(code, message, requestId);
            case 503 -> new OrbUnavailableException(code, message, requestId);
            default  -> new OrbApiException(statusCode, code, message, requestId);
        };
    }
}
