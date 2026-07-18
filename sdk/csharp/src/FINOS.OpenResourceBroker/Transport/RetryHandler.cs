// Layer 3: Retry with Exponential Back-off
//
// Wraps an HttpMessageHandler and retries transient failures.  The retry policy
// is idempotency-aware to avoid duplicate provisioning:
//
//   Idempotent methods (GET/PUT/DELETE/HEAD/OPTIONS):
//     - Retried on HTTP 429 and 503, and on any 5xx.
//     - Retried on transient network errors (SocketException, IOException,
//       HttpRequestException) and on server-side request timeouts.
//
//   POST (non-idempotent — e.g. request/return machines):
//     - NEVER retried on 429/503 or on any status.  Retrying a POST that the
//       server may already have processed risks double-provisioning.
//     - NEVER retried on a post-write network error (the request may have
//       reached the server before the socket dropped).
//     - Retried ONLY on a pre-write / connection-refused failure, where it is
//       certain the server never received the request.
//
// Never retries:
//   - 4xx errors other than 429 (and 429 only for idempotent methods).
//   - Requests already cancelled by the caller's CancellationToken.

using System.Net.Sockets;

namespace FINOS.OpenResourceBroker.Transport;

/// <summary>Retry configuration.</summary>
public sealed class RetryConfig
{
    /// <summary>Maximum number of retry attempts (default: 3).</summary>
    public int MaxRetries { get; init; } = 3;

    /// <summary>Base delay in milliseconds for exponential back-off (default: 500).</summary>
    public int BaseDelayMs { get; init; } = 500;

    /// <summary>Maximum delay in milliseconds (default: 30_000).</summary>
    public int MaxDelayMs { get; init; } = 30_000;
}

/// <summary>
/// DelegatingHandler that retries transient failures with exponential back-off and jitter.
/// </summary>
public sealed class RetryDelegatingHandler : DelegatingHandler
{
    private static readonly ISet<HttpMethod> IdempotentMethods = new HashSet<HttpMethod>
    {
        HttpMethod.Get, HttpMethod.Head, HttpMethod.Put, HttpMethod.Delete, HttpMethod.Options
    };

    private readonly RetryConfig _cfg;

    public RetryDelegatingHandler(RetryConfig cfg, HttpMessageHandler inner) : base(inner)
        => _cfg = cfg;

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        var attempt = 0;
        var delay = _cfg.BaseDelayMs;
        var idempotent = IdempotentMethods.Contains(request.Method);

        while (true)
        {
            HttpResponseMessage? response = null;
            try
            {
                // Clone the request content so it can be re-read on retry
                var req = attempt == 0 ? request : await CloneRequestAsync(request, cancellationToken);
                response = await base.SendAsync(req, cancellationToken).ConfigureAwait(false);

                if (!ShouldRetryStatus(idempotent, (int)response.StatusCode) || attempt >= _cfg.MaxRetries)
                    return response;

                response.Dispose();
            }
            catch (Exception ex) when (ShouldRetryException(ex, idempotent) && !cancellationToken.IsCancellationRequested)
            {
                if (attempt >= _cfg.MaxRetries) throw;
            }

            attempt++;
            var jitteredDelay = (int)(delay * (0.5 + Random.Shared.NextDouble() * 0.5));
            await Task.Delay(Math.Min(jitteredDelay, _cfg.MaxDelayMs), cancellationToken)
                      .ConfigureAwait(false);
            delay = Math.Min(delay * 2, _cfg.MaxDelayMs);
        }
    }

    // Retry 429/503/5xx only for idempotent methods.  POST is never retried on a
    // status response because the server may already have processed it.
    private static bool ShouldRetryStatus(bool idempotent, int statusCode)
    {
        if (!idempotent) return false;
        if (statusCode == 429 || statusCode == 503) return true;
        if (statusCode >= 500) return true;
        return false;
    }

    // Decide whether an exception thrown while sending is retryable.
    //
    // For idempotent methods: any transient network error or server-side request
    // timeout is retryable.
    //
    // For POST: only a pre-write / connection-refused failure is retryable — the
    // server is guaranteed never to have received the request.  A post-write
    // network drop or a request timeout is NOT retried (the request may have
    // reached the server, so retrying could double-provision).
    private static bool ShouldRetryException(Exception ex, bool idempotent)
    {
        if (idempotent)
            return IsTransientException(ex);

        // Non-idempotent (POST): only connection-refused (pre-write) is safe.
        return IsConnectionRefused(ex);
    }

    private static bool IsTransientException(Exception ex)
    {
        // Server-side request timeout surfaces as TaskCanceledException while the
        // caller's token is NOT cancelled (the SendAsync catch filter guards that).
        // TimeoutException may also surface from custom handlers.
        if (ex is TaskCanceledException or TimeoutException) return true;
        return HasInner<SocketException>(ex)
            || ex is System.IO.IOException
            || ex is HttpRequestException;
    }

    // A connection-refused / host-unreachable failure means the TCP/UDS connection
    // was never established, so the server never saw the request — safe to retry
    // even for POST.
    private static bool IsConnectionRefused(Exception ex)
    {
        for (Exception? e = ex; e != null; e = e.InnerException)
        {
            if (e is SocketException se &&
                se.SocketErrorCode is SocketError.ConnectionRefused
                                   or SocketError.HostUnreachable
                                   or SocketError.NetworkUnreachable
                                   or SocketError.HostNotFound)
                return true;
        }
        return false;
    }

    private static bool HasInner<T>(Exception ex) where T : Exception
    {
        for (Exception? e = ex; e != null; e = e.InnerException)
            if (e is T) return true;
        return false;
    }

    private static async Task<HttpRequestMessage> CloneRequestAsync(
        HttpRequestMessage original,
        CancellationToken ct)
    {
        var clone = new HttpRequestMessage(original.Method, original.RequestUri);
        foreach (var header in original.Headers)
            clone.Headers.TryAddWithoutValidation(header.Key, header.Value);
        clone.Version = original.Version;

        if (original.Content != null)
        {
            var bytes = await original.Content.ReadAsByteArrayAsync(ct).ConfigureAwait(false);
            clone.Content = new ByteArrayContent(bytes);
            foreach (var header in original.Content.Headers)
                clone.Content.Headers.TryAddWithoutValidation(header.Key, header.Value);
        }

        return clone;
    }
}
