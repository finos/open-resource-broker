// ORB SDK error types.
//
// Cross-SDK contract (shared with Go/TypeScript/Java/Kotlin):
//   - OrbException is the base type for everything the SDK throws (the .NET
//     idiom keeps the "Exception" suffix; it is the OrbError of the family).
//   - OrbApiException is thrown for all HTTP error responses and carries the
//     canonical field set: StatusCode (httpStatus), Code (machine-readable
//     errorCode, may be null), Message, RequestId (for support correlation),
//     and the raw ResponseBody for debugging.
//   - Typed sentinel subclasses exist for 401/403/404/409/503/408 so callers
//     can `catch (OrbNotFoundException)` etc.  Use ForStatus to construct the
//     most specific subclass for a status.

namespace FINOS.OpenResourceBroker;

/// <summary>Base exception for all ORB SDK errors (the .NET equivalent of OrbError).</summary>
public class OrbException : Exception
{
    public OrbException(string message) : base(message) { }
    public OrbException(string message, Exception inner) : base(message, inner) { }
}

/// <summary>
/// An HTTP-level error returned by the ORB server.
/// <para>
/// Carries the canonical cross-SDK field set shared with Go/TypeScript/Java/Kotlin:
/// HTTP status (<see cref="StatusCode"/>), machine-readable error code
/// (<see cref="Code"/>, may be null), message, the server-assigned request ID
/// (<see cref="RequestId"/>) for support correlation, and the raw
/// <see cref="ResponseBody"/> for debugging.
/// </para>
/// </summary>
public class OrbApiException : OrbException
{
    /// <summary>HTTP status code.</summary>
    public int StatusCode { get; }

    /// <summary>Optional machine-readable error code from the ORB server.</summary>
    public string? Code { get; }

    /// <summary>Server-assigned request ID (X-Request-ID) for support/correlation, or null.</summary>
    public string? RequestId { get; }

    /// <summary>Raw response body (for debugging).</summary>
    public string? ResponseBody { get; }

    public OrbApiException(int statusCode, string message, string? code = null,
                           string? responseBody = null, string? requestId = null)
        : base(message)
    {
        StatusCode = statusCode;
        Code = code;
        ResponseBody = responseBody;
        RequestId = requestId;
    }

    /// <summary>True if this is a "not found" error (HTTP 404).</summary>
    public bool IsNotFound => StatusCode == 404;

    /// <summary>True if this is an "unauthorized" error (HTTP 401).</summary>
    public bool IsUnauthorized => StatusCode == 401;

    /// <summary>True if this is a "forbidden" error (HTTP 403).</summary>
    public bool IsForbidden => StatusCode == 403;

    /// <summary>True if this is a "conflict" error (HTTP 409).</summary>
    public bool IsConflict => StatusCode == 409;

    /// <summary>True if this is a "service unavailable" error (HTTP 503).</summary>
    public bool IsUnavailable => StatusCode == 503;

    /// <summary>True if this is a "request timeout" error (HTTP 408).</summary>
    public bool IsTimeout => StatusCode == 408;

    /// <summary>
    /// Construct the most specific <see cref="OrbApiException"/> subclass for an
    /// HTTP status so <c>catch (OrbNotFoundException)</c> (etc.) works for callers,
    /// falling back to the base <see cref="OrbApiException"/> for statuses without
    /// a typed sentinel.  Mirrors TS <c>apiErrorForStatus</c>, Java
    /// <c>OrbApiException.forStatus</c>, and Go <c>sentinelForStatus</c>.
    /// </summary>
    public static OrbApiException ForStatus(int statusCode, string message,
                                            string? code = null,
                                            string? responseBody = null,
                                            string? requestId = null) =>
        statusCode switch
        {
            401 => new OrbUnauthorizedException(message, code, responseBody, requestId),
            403 => new OrbForbiddenException(message, code, responseBody, requestId),
            404 => new OrbNotFoundException(message, code, responseBody, requestId),
            409 => new OrbConflictException(message, code, responseBody, requestId),
            408 => new OrbTimeoutException(message, code, responseBody, requestId),
            503 => new OrbUnavailableException(message, code, responseBody, requestId),
            _ => new OrbApiException(statusCode, message, code, responseBody, requestId),
        };

    public override string ToString() =>
        $"OrbApiException: HTTP {StatusCode}{(Code != null ? $" [{Code}]" : "")} — {Message}";
}

/// <summary>A resource was not found (HTTP 404).</summary>
public sealed class OrbNotFoundException : OrbApiException
{
    public OrbNotFoundException(string message = "orb: not found", string? code = null,
                               string? responseBody = null, string? requestId = null)
        : base(404, message, code, responseBody, requestId) { }
}

/// <summary>Authentication is missing or invalid (HTTP 401).</summary>
public sealed class OrbUnauthorizedException : OrbApiException
{
    public OrbUnauthorizedException(string message = "orb: unauthorized", string? code = null,
                                   string? responseBody = null, string? requestId = null)
        : base(401, message, code, responseBody, requestId) { }
}

/// <summary>The caller is not permitted to perform the operation (HTTP 403).</summary>
public sealed class OrbForbiddenException : OrbApiException
{
    public OrbForbiddenException(string message = "orb: forbidden", string? code = null,
                                string? responseBody = null, string? requestId = null)
        : base(403, message, code, responseBody, requestId) { }
}

/// <summary>The request conflicts with the current server state (HTTP 409).</summary>
public sealed class OrbConflictException : OrbApiException
{
    public OrbConflictException(string message = "orb: conflict", string? code = null,
                               string? responseBody = null, string? requestId = null)
        : base(409, message, code, responseBody, requestId) { }
}

/// <summary>The request timed out (HTTP 408).</summary>
public sealed class OrbTimeoutException : OrbApiException
{
    public OrbTimeoutException(string message = "orb: request timeout", string? code = null,
                              string? responseBody = null, string? requestId = null)
        : base(408, message, code, responseBody, requestId) { }
}

/// <summary>
/// The ORB server is unavailable (HTTP 503, or a managed process that died).
/// <para>
/// Extends <see cref="OrbApiException"/> (status 503) so it is catchable both as
/// a typed sentinel and via the <see cref="OrbApiException"/> / <see cref="OrbException"/>
/// bases, matching the other SDKs' unavailable sentinel.
/// </para>
/// </summary>
public sealed class OrbUnavailableException : OrbApiException
{
    public OrbUnavailableException(string message = "orb: service unavailable", string? code = null,
                                  string? responseBody = null, string? requestId = null)
        : base(503, message, code, responseBody, requestId) { }
}
