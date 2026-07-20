package org.finos.openresourcebroker.sdk.client;

/**
 * Typed sentinel for HTTP 409 (conflict).
 *
 * <p>Mirrors {@code OrbConflictError} in the Go/TypeScript/Kotlin/C# SDKs.
 */
public class OrbConflictException extends OrbApiException {

    public OrbConflictException(String message) {
        super(409, null, message, null);
    }

    public OrbConflictException(String code, String message, String requestId) {
        super(409, code, message, requestId);
    }
}
