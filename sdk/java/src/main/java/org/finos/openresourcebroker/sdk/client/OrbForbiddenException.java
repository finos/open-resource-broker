package org.finos.openresourcebroker.sdk.client;

/**
 * Typed sentinel for HTTP 403 (forbidden).
 *
 * <p>Mirrors {@code OrbForbiddenError} in the Go/TypeScript/Kotlin/C# SDKs.
 */
public class OrbForbiddenException extends OrbApiException {

    public OrbForbiddenException(String message) {
        super(403, null, message, null);
    }

    public OrbForbiddenException(String code, String message, String requestId) {
        super(403, code, message, requestId);
    }
}
