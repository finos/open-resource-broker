package org.finos.openresourcebroker.sdk.client;

/**
 * Identifies which ORB scheduler backend the client targets.
 *
 * <p>Mirrors Go's {@code SchedulerType} constants and TypeScript's
 * {@code 'default' | 'hostfactory'} union, replacing the previously untyped
 * {@code String} so a typo cannot silently send a wrong {@code X-ORB-Scheduler}
 * header.  The {@link #wireValue()} is what is sent on the wire.
 */
public enum Scheduler {

    /** The default ORB scheduler (no {@code X-ORB-Scheduler} header is sent). */
    DEFAULT("default"),

    /** The HostFactory scheduler ({@code X-ORB-Scheduler: hostfactory}). */
    HOSTFACTORY("hostfactory");

    private final String wireValue;

    Scheduler(String wireValue) {
        this.wireValue = wireValue;
    }

    /** The value sent in the {@code X-ORB-Scheduler} header. */
    public String wireValue() {
        return wireValue;
    }

    /**
     * Parse a scheduler wire value (case-insensitive), for interop with callers
     * that still pass a raw string.
     *
     * @throws IllegalArgumentException if the value is not a recognised scheduler
     */
    public static Scheduler fromWire(String value) {
        if (value == null) return DEFAULT;
        for (Scheduler s : values()) {
            if (s.wireValue.equalsIgnoreCase(value)) return s;
        }
        throw new IllegalArgumentException("Unknown scheduler: " + value);
    }
}
