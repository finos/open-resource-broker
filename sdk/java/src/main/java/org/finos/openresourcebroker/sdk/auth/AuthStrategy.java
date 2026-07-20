// Layer 4: AWS SigV4 Authentication
package org.finos.openresourcebroker.sdk.auth;

import java.util.Map;

/**
 * Authentication strategy: adds headers to outgoing HTTP requests.
 */
public interface AuthStrategy {

    /** No-op auth (development/testing). */
    AuthStrategy NONE = headers -> {};

    /**
     * Populate the given mutable header map with authentication headers.
     * Called before every request.
     *
     * @param headers mutable map; add "Authorization" or other headers here
     */
    void apply(Map<String, String> headers) throws Exception;
}
