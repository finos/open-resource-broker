package org.finos.openresourcebroker.sdk.client;

/**
 * The server ended a request status stream because its maximum stream duration
 * elapsed before the request reached a terminal status. The last event
 * received is not a final status.
 */
public class OrbStreamTimeoutException extends OrbError {

    public OrbStreamTimeoutException() {
        super("orb: request status stream timed out before the request finished");
    }
}
