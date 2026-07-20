package org.finos.openresourcebroker.sdk.client;

/**
 * Typed sentinel for HTTP 401 (unauthorized).
 *
 * <p>Mirrors {@code OrbUnauthorizedError} in the Go/TypeScript/Kotlin/C# SDKs.
 */
public class OrbUnauthorizedException extends OrbApiException {

    public OrbUnauthorizedException(String message) {
        super(401, null, message, null);
    }

    public OrbUnauthorizedException(String code, String message, String requestId) {
        super(401, code, message, requestId);
    }
}
