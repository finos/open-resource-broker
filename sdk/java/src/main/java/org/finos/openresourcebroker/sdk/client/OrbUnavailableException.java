package org.finos.openresourcebroker.sdk.client;

/**
 * Thrown when the ORB service is unavailable (HTTP 503) or the managed ORB
 * process is unhealthy.
 *
 * <p>Typed sentinel for HTTP 503, mirroring the {@code OrbUnavailableError}
 * exposed by the Go/TypeScript/Kotlin/C# SDKs.  Extends {@link OrbApiException}
 * with a fixed status code of 503 so it is both catchable as
 * {@code OrbUnavailableException} and compatible with code that catches
 * {@code OrbApiException} and checks {@link #isServerError()}.
 */
public class OrbUnavailableException extends OrbApiException {

    public OrbUnavailableException(String message) {
        super(503, null, message, null);
    }

    public OrbUnavailableException(String code, String message, String requestId) {
        super(503, code, message, requestId);
    }

    public OrbUnavailableException(String message, Throwable cause) {
        super(503, null, message, null);
        initCause(cause);
    }
}
