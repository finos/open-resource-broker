package org.finos.openresourcebroker.sdk.client;

/**
 * The server answered in a different response format than the one the client asked for.
 *
 * <p>Raised only when the response names a format in {@code X-ORB-Scheduler} that differs
 * from the request. A response without that header is never an error.
 */
public class SchedulerMismatchError extends OrbError {

    private final String expected;
    private final String actual;

    public SchedulerMismatchError(String expected, String actual) {
        super("requested response format \"" + expected + "\" but the server answered in \""
                + actual + "\"");
        this.expected = expected;
        this.actual = actual;
    }

    /** The format the call asked for. */
    public String expected() {
        return expected;
    }

    /** The format the response named. */
    public String actual() {
        return actual;
    }
}
