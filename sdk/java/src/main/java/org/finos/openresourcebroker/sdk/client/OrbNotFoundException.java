package org.finos.openresourcebroker.sdk.client;

/**
 * Typed sentinel for HTTP 404 (resource not found).
 *
 * <p>Mirrors {@code OrbNotFoundError} in the Go/TypeScript/Kotlin/C# SDKs so
 * callers can {@code catch (OrbNotFoundException e)} or use {@code instanceof}.
 */
public class OrbNotFoundException extends OrbApiException {

    public OrbNotFoundException(String message) {
        super(404, null, message, null);
    }

    public OrbNotFoundException(String code, String message, String requestId) {
        super(404, code, message, requestId);
    }
}
