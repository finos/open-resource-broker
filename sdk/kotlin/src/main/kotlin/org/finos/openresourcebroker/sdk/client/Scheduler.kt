package org.finos.openresourcebroker.sdk.client

/**
 * Identifies which ORB scheduler backend the client targets.
 *
 * Mirrors Go's `SchedulerType` constants and TypeScript's
 * `'default' | 'hostfactory'` union, replacing the previously untyped [String]
 * so a typo cannot silently send a wrong `X-ORB-Scheduler` header. The
 * [wireValue] is what is sent on the wire.
 */
enum class Scheduler(val wireValue: String) {

    /** The default ORB scheduler (no `X-ORB-Scheduler` header is sent). */
    DEFAULT("default"),

    /** The HostFactory scheduler (`X-ORB-Scheduler: hostfactory`). */
    HOSTFACTORY("hostfactory");

    companion object {
        /**
         * Parse a scheduler wire value (case-insensitive), for interop with callers
         * that still pass a raw string.
         *
         * @throws IllegalArgumentException if the value is not a recognised scheduler
         */
        fun fromWire(value: String?): Scheduler {
            if (value == null) return DEFAULT
            return entries.firstOrNull { it.wireValue.equals(value, ignoreCase = true) }
                ?: throw IllegalArgumentException("Unknown scheduler: $value")
        }
    }
}
