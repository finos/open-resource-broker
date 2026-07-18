// Layer 5: Server-Sent Events Reader + Reconnect
//
// Parses SSE wire format from an HttpResponseMessage stream, yields typed events,
// and reconnects with exponential back-off when the connection is dropped.
//
// ORB SSE wire format:
//   data: <json>\n\n   — normal event
//   data: {}\n\n       — terminal sentinel (stream done)
//
// Auth headers are re-applied on each reconnect via the HttpClient pipeline.

using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;

namespace FINOS.OpenResourceBroker.Sse;

// ---------------------------------------------------------------------------
// Wire-level SSE frame
// ---------------------------------------------------------------------------

/// <summary>A single parsed SSE frame from the wire.</summary>
public sealed class SseFrame
{
    public string Data { get; init; } = "";
    public string? Event { get; init; }
    public string? Id { get; init; }
    public int? Retry { get; init; }
}

// ---------------------------------------------------------------------------
// ORB-specific payload types
// ---------------------------------------------------------------------------

/// <summary>Machine reference in an SSE payload.</summary>
public sealed class SseMachine
{
    [System.Text.Json.Serialization.JsonPropertyName("machine_id")]
    public string MachineId { get; init; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("name")]
    public string? Name { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("status")]
    public string? Status { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("result")]
    public string? Result { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("private_ip")]
    public string? PrivateIp { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("public_ip")]
    public string? PublicIp { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("launch_time")]
    public string? LaunchTime { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("message")]
    public string? Message { get; init; }
}

/// <summary>A request item embedded in an SSE payload.</summary>
public sealed class SseRequest
{
    [System.Text.Json.Serialization.JsonPropertyName("request_id")]
    public string RequestId { get; init; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("status")]
    public string Status { get; init; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("message")]
    public string? Message { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("requested_count")]
    public int? RequestedCount { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("successful_count")]
    public int? SuccessfulCount { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("failed_count")]
    public int? FailedCount { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("machines")]
    public List<SseMachine>? Machines { get; init; }
}

/// <summary>Top-level SSE payload.</summary>
public sealed class SsePayload
{
    [System.Text.Json.Serialization.JsonPropertyName("requests")]
    public List<SseRequest>? Requests { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("event_type")]
    public string? EventType { get; init; }
}

/// <summary>Terminal statuses for ORB requests.</summary>
public static class TerminalStatuses
{
    public static readonly HashSet<string> All = new(StringComparer.OrdinalIgnoreCase)
    {
        "complete", "completed", "failed", "error", "cancelled", "canceled", "partial", "timeout"
    };
}

// ---------------------------------------------------------------------------
// Stream event (high-level, consumer-facing)
// ---------------------------------------------------------------------------

/// <summary>High-level event yielded from <see cref="OrbClient.StreamRequestStatusAsync"/>.</summary>
public sealed class StreamEvent
{
    public string RequestId { get; init; } = "";
    public string Status { get; init; } = "";
    public string? Message { get; init; }
    public int? RequestedCount { get; init; }
    public int? SuccessfulCount { get; init; }
    public int? FailedCount { get; init; }
    public List<SseMachine> Machines { get; init; } = [];
}

// ---------------------------------------------------------------------------
// Low-level frame parser
// ---------------------------------------------------------------------------

public static class SseFrameParser
{
    /// <summary>
    /// Maximum size (bytes) of a single SSE line and of an accumulated frame's
    /// data.  Mirrors the Go reader's 4 MiB cap.  A server that never emits a
    /// newline, or emits an enormous data line, would otherwise grow client
    /// memory without bound (a memory-exhaustion DoS on remote streams).  On
    /// overflow the parser throws a terminal error so the reconnect loop aborts
    /// rather than spinning on the same oversized frame.
    /// </summary>
    public const int MaxFrameBytes = 4 * 1024 * 1024;

    /// <summary>
    /// Parse SSE frames from a stream.  Yields one <see cref="SseFrame"/> per
    /// dispatched event (blank-line separator).
    /// </summary>
    public static async IAsyncEnumerable<SseFrame> ParseAsync(
        Stream stream,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var reader = new BoundedLineReader(stream, MaxFrameBytes);

        string? currentData = null;
        string? currentEvent = null;
        string? currentId = null;
        int? currentRetry = null;
        // Accumulated data bytes for the in-progress frame; bounded at MaxFrameBytes.
        var currentDataBytes = 0;

        while (!ct.IsCancellationRequested)
        {
            string? line;
            try
            {
                line = await reader.ReadLineAsync(ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException) { yield break; }
            catch (SseFrameTooLargeException)
            {
                // Terminal, non-retryable: abort the stream rather than
                // reconnecting so the loop does not spin on the oversized frame.
                throw;
            }
            catch (Exception) { yield break; }

            if (line == null) yield break; // stream ended

            if (line == "") // dispatch
            {
                if (currentData != null)
                {
                    yield return new SseFrame
                    {
                        Data = currentData,
                        Event = currentEvent,
                        Id = currentId,
                        Retry = currentRetry,
                    };
                }
                currentData = null;
                currentEvent = null;
                currentId = null;
                currentRetry = null;
                currentDataBytes = 0;
                continue;
            }

            // comment line
            if (line.StartsWith(':')) continue;

            int colonIdx = line.IndexOf(':');
            string field, value;
            if (colonIdx < 0)
            {
                field = line;
                value = "";
            }
            else
            {
                field = line[..colonIdx];
                value = line[(colonIdx + 1)..].TrimStart(' ');
            }

            switch (field)
            {
                case "data":
                    // Bound the accumulated data for a single (multi-line) frame.
                    currentDataBytes += Encoding.UTF8.GetByteCount(value) + 1; // +1 for the joining '\n'
                    if (currentDataBytes > MaxFrameBytes)
                        throw new SseFrameTooLargeException(
                            $"SSE frame exceeded {MaxFrameBytes} bytes");
                    currentData = currentData == null ? value : currentData + "\n" + value;
                    break;
                case "event":
                    currentEvent = value;
                    break;
                case "id":
                    currentId = value;
                    break;
                case "retry":
                    if (int.TryParse(value, out var r)) currentRetry = r;
                    break;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Oversized-frame terminal error
// ---------------------------------------------------------------------------

/// <summary>
/// Thrown when a single SSE line or accumulated frame exceeds
/// <see cref="SseFrameParser.MaxFrameBytes"/>.  Terminal and non-retryable:
/// the reconnect loop surfaces it rather than reconnecting.
/// </summary>
public sealed class SseFrameTooLargeException : OrbException
{
    public SseFrameTooLargeException(string message) : base(message) { }
}

// ---------------------------------------------------------------------------
// Bounded line reader
// ---------------------------------------------------------------------------

/// <summary>
/// Reads UTF-8 lines from a stream with a hard cap on a single line's length.
/// A server that never emits a newline cannot grow client memory without bound:
/// once the pending (newline-less) buffer exceeds <c>maxBytes</c> a
/// <see cref="SseFrameTooLargeException"/> is thrown.  Recognises "\n" and
/// "\r\n" line terminators (matching SSE wire format).
/// </summary>
internal sealed class BoundedLineReader
{
    private readonly Stream _stream;
    private readonly int _maxBytes;
    private readonly byte[] _readBuffer = new byte[8192];
    private readonly List<byte> _pending = new();
    private int _readPos;
    private int _readLen;
    private bool _eof;

    public BoundedLineReader(Stream stream, int maxBytes)
    {
        _stream = stream;
        _maxBytes = maxBytes;
    }

    public async Task<string?> ReadLineAsync(CancellationToken ct)
    {
        while (true)
        {
            // Scan buffered bytes for a '\n'.
            while (_readPos < _readLen)
            {
                var b = _readBuffer[_readPos++];
                if (b == (byte)'\n')
                {
                    // Strip a trailing '\r' if present (CRLF).
                    var count = _pending.Count;
                    if (count > 0 && _pending[count - 1] == (byte)'\r') count--;
                    var line = Encoding.UTF8.GetString(_pending.ToArray(), 0, count);
                    _pending.Clear();
                    return line;
                }
                _pending.Add(b);
                if (_pending.Count > _maxBytes)
                    throw new SseFrameTooLargeException(
                        $"SSE line exceeded {_maxBytes} bytes without a newline");
            }

            if (_eof)
            {
                if (_pending.Count == 0) return null;
                var line = Encoding.UTF8.GetString(_pending.ToArray());
                _pending.Clear();
                return line;
            }

            _readLen = await _stream.ReadAsync(_readBuffer.AsMemory(0, _readBuffer.Length), ct)
                                    .ConfigureAwait(false);
            _readPos = 0;
            if (_readLen == 0) _eof = true;
        }
    }
}

// ---------------------------------------------------------------------------
// High-level: reconnecting SSE stream
// ---------------------------------------------------------------------------

public sealed class SseStreamOptions
{
    public int InitialDelayMs { get; init; } = 1_000;
    public int MaxDelayMs { get; init; } = 30_000;
    public string? LastEventId { get; init; }
}

public static class SseStream
{
    /// <summary>
    /// Reconnecting SSE stream as IAsyncEnumerable.
    ///
    /// <paramref name="connect"/> is called on each (re)connection.
    /// Returns the stream from the response.
    /// Auth headers are re-applied by the HttpClient pipeline on each call.
    /// </summary>
    public static async IAsyncEnumerable<SseFrame> StreamAsync(
        Func<string?, CancellationToken, Task<Stream>> connect,
        SseStreamOptions? opts = null,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        opts ??= new SseStreamOptions();
        var delay = opts.InitialDelayMs;
        var lastEventId = opts.LastEventId;

        while (!ct.IsCancellationRequested)
        {
            Stream stream;
            try
            {
                stream = await connect(lastEventId, ct).ConfigureAwait(false);
            }
            catch (OrbApiException ex) when (ex.StatusCode >= 400 && ex.StatusCode < 500)
            {
                // 4xx — don't retry, propagate
                throw;
            }
            catch (OperationCanceledException) { yield break; }
            catch
            {
                if (ct.IsCancellationRequested) yield break;
                await DelayAsync(delay, ct).ConfigureAwait(false);
                delay = Math.Min(delay * 2, opts.MaxDelayMs);
                continue;
            }

            bool gotFrames = false;
            await using (stream)
            {
                await foreach (var frame in SseFrameParser.ParseAsync(stream, ct).ConfigureAwait(false))
                {
                    if (ct.IsCancellationRequested) yield break;

                    if (frame.Id != null) lastEventId = frame.Id;
                    if (frame.Retry.HasValue) delay = frame.Retry.Value;

                    gotFrames = true;
                    yield return frame;

                    // Terminal sentinel: data: {}
                    if (frame.Data.Trim() == "{}") yield break;
                }
            }

            if (ct.IsCancellationRequested) yield break;
            if (gotFrames) delay = opts.InitialDelayMs; // reset after successful connection

            await DelayAsync(delay, ct).ConfigureAwait(false);
            delay = Math.Min(delay * 2, opts.MaxDelayMs);
        }
    }

    private static Task DelayAsync(int ms, CancellationToken ct)
    {
        var jittered = (int)(ms * (0.5 + Random.Shared.NextDouble() * 0.5));
        return Task.Delay(jittered, ct);
    }

    internal static SsePayload? ParsePayload(SseFrame frame)
    {
        if (frame.Data.Trim() == "{}") return null;
        try
        {
            return JsonSerializer.Deserialize<SsePayload>(frame.Data);
        }
        catch
        {
            return null;
        }
    }
}
