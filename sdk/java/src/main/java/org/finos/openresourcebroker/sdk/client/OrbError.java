package org.finos.openresourcebroker.sdk.client;

/**
 * Base type for every error the ORB SDK throws.
 *
 * <p>Mirrors the {@code OrbError} base exposed by the Go, TypeScript, Kotlin and
 * C# SDKs so the whole family shares one vocabulary.  Callers can
 * {@code catch (OrbError e)} to handle any SDK-originated failure — both HTTP
 * API errors ({@link OrbApiException}) and non-HTTP transport/usage errors.
 */
public class OrbError extends RuntimeException {

    public OrbError(String message) {
        super(message);
    }

    public OrbError(String message, Throwable cause) {
        super(message, cause);
    }
}
