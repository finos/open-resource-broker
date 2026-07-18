// Layer 4: AWS SigV4 Authentication
package org.finos.openresourcebroker.sdk.auth;

import java.util.Map;
import java.util.function.Supplier;

/**
 * Adds {@code Authorization: Bearer <token>} to every request.
 * Supports both static tokens and dynamic token suppliers (for token refresh).
 */
public class BearerTokenAuth implements AuthStrategy {

    private final Supplier<String> tokenSupplier;

    /** Static token. */
    public BearerTokenAuth(String token) {
        this.tokenSupplier = () -> token;
    }

    /** Dynamic token supplier — called on every request. */
    public BearerTokenAuth(Supplier<String> tokenSupplier) {
        this.tokenSupplier = tokenSupplier;
    }

    @Override
    public void apply(Map<String, String> headers) {
        headers.put("Authorization", "Bearer " + tokenSupplier.get());
    }
}
