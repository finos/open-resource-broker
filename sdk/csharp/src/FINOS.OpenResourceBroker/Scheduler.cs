// Scheduler selector.
//
// Identifies which ORB scheduler backend the client targets.  Mirrors Go's
// SchedulerType constants, TypeScript's 'default' | 'hostfactory' union, and
// Kotlin's Scheduler enum, replacing a raw string so a typo cannot silently
// send a wrong X-ORB-Scheduler header (which changes wire parsing between
// snake_case and camelCase).

namespace FINOS.OpenResourceBroker;

/// <summary>Which ORB scheduler backend the client targets.</summary>
public enum Scheduler
{
    /// <summary>The default ORB scheduler (no X-ORB-Scheduler header is sent).</summary>
    Default,

    /// <summary>The HostFactory scheduler (sends <c>X-ORB-Scheduler: hostfactory</c>).</summary>
    HostFactory,
}

/// <summary>Wire-value helpers for <see cref="Scheduler"/>.</summary>
public static class SchedulerExtensions
{
    /// <summary>The value sent in the <c>X-ORB-Scheduler</c> header.</summary>
    public static string WireValue(this Scheduler scheduler) =>
        scheduler switch
        {
            Scheduler.HostFactory => "hostfactory",
            _ => "default",
        };

    /// <summary>
    /// Parse a scheduler wire value (case-insensitive), for interop with callers
    /// that still pass a raw string.  A null/empty value maps to <see cref="Scheduler.Default"/>.
    /// </summary>
    /// <exception cref="ArgumentException">if the value is not a recognised scheduler.</exception>
    public static Scheduler FromWire(string? value)
    {
        if (string.IsNullOrEmpty(value)) return Scheduler.Default;
        return value.ToLowerInvariant() switch
        {
            "default" => Scheduler.Default,
            "hostfactory" => Scheduler.HostFactory,
            _ => throw new ArgumentException($"Unknown scheduler: {value}", nameof(value)),
        };
    }
}
