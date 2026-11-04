package org.finos.openresourcebroker.sdk.client;

/** The server ended a request status stream with an error frame. */
public class OrbStreamException extends OrbError {

    public OrbStreamException() {
        super("orb: request status stream failed on the server");
    }
}
