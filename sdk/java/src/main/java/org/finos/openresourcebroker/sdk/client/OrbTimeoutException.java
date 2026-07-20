package org.finos.openresourcebroker.sdk.client;

/**
 * Typed sentinel for HTTP 408 (request timeout).
 *
 * <p>Mirrors {@code OrbTimeoutError} in the Go/TypeScript/Kotlin/C# SDKs.
 */
public class OrbTimeoutException extends OrbApiException {

    public OrbTimeoutException(String message) {
        super(408, null, message, null);
    }

    public OrbTimeoutException(String code, String message, String requestId) {
        super(408, code, message, requestId);
    }
}
