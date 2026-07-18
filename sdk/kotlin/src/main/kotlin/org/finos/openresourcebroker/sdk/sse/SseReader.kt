/**
 * Layer 5: Server-Sent Events Reader + Reconnect
 *
 * Parses SSE wire format from an InputStream, emits typed [SseFrame] objects,
 * and reconnects with exponential back-off when the connection is dropped.
 *
 * ORB SSE wire format:
 *   data: <json>\n\n   — normal event
 *   data: {}\n\n       — terminal sentinel (stream done)
 *
 * Kotlin Flow is used as the async primitive so callers can collect with
 * structured concurrency.
 */

package org.finos.openresourcebroker.sdk.sse

import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.finos.openresourcebroker.sdk.client.OrbApiError
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import kotlin.math.min

/**
 * Maximum size of a single SSE line / accumulated frame (4 MiB, matching the
 * Go/TS/Java/C# SDKs). A server that never emits a newline, or emits an enormous
 * `data:` line, would otherwise grow the client heap without bound. On overflow
 * the reader throws so the stream terminates rather than reconnecting and
 * spinning on the same oversized frame.
 */
internal const val MAX_SSE_FRAME_BYTES = 4 * 1024 * 1024

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

data class SseFrame(
    val data: String,
    val event: String? = null,
    val id: String? = null,
    val retry: Long? = null,
)

// ---------------------------------------------------------------------------
// ORB-specific payload types
// ---------------------------------------------------------------------------

data class OrbMachine(
    val machine_id: String = "",
    val name: String? = null,
    val status: String? = null,
    val result: String? = null,
    val private_ip: String? = null,
    val public_ip: String? = null,
    val launch_time: String? = null,
    val message: String? = null,
)

data class OrbSseRequest(
    val request_id: String = "",
    val status: String = "",
    val message: String? = null,
    val requested_count: Int? = null,
    val successful_count: Int? = null,
    val failed_count: Int? = null,
    val machines: List<OrbMachine>? = null,
)

data class OrbSsePayload(
    val requests: List<OrbSseRequest>? = null,
    val event_type: String? = null,
    val sentinel: Boolean? = null,
)

val TERMINAL_STATUSES = setOf(
    "complete", "completed", "failed", "error", "cancelled", "canceled", "partial", "timeout"
)

/**
 * True if this frame is the ORB terminal sentinel (data: {}).
 */
fun SseFrame.isSentinel(): Boolean = data.trim() == "{}"

// ---------------------------------------------------------------------------
// Frame parsing
// ---------------------------------------------------------------------------

/**
 * Read a single LF-terminated line from a raw byte [stream], stripping a trailing
 * CR. Reads raw bytes (not a decoding [java.io.BufferedReader]) and caps the line
 * at [MAX_SSE_FRAME_BYTES] so an unbounded, newline-free line cannot exhaust the
 * heap.
 *
 * @return the decoded line without its terminator, or `null` at EOF before any
 *   byte was read
 * @throws IOException if the line exceeds [MAX_SSE_FRAME_BYTES]
 */
private fun readBoundedLine(stream: InputStream): String? {
    val buf = ByteArrayOutputStream(256)
    var any = false
    while (true) {
        val b = stream.read()
        if (b == -1) break
        any = true
        if (b == '\n'.code) return buf.toString(Charsets.UTF_8.name())
        if (b == '\r'.code) continue // strip CR; the following LF ends the line
        if (buf.size() >= MAX_SSE_FRAME_BYTES) {
            throw IOException("SSE line exceeds maximum allowed $MAX_SSE_FRAME_BYTES bytes")
        }
        buf.write(b)
    }
    if (!any) return null
    return buf.toString(Charsets.UTF_8.name())
}

/**
 * Parse SSE frames from a blocking [InputStream].
 *
 * Runs the blocking reads on [Dispatchers.IO]. Because a blocking socket read
 * cannot be interrupted by coroutine cancellation, an [invokeOnCompletion]
 * handler closes the underlying [stream] when the collecting scope is cancelled
 * (or the flow otherwise completes), unblocking the in-progress read with an
 * IOException so the IO thread and UDS connection are released promptly.
 */
internal fun parseSseFrames(stream: InputStream): Flow<SseFrame> = flow {
    var currentData: StringBuilder? = null
    var frameBytes = 0
    var currentEvent: String? = null
    var currentId: String? = null
    var currentRetry: Long? = null

    // Close the stream on cancellation/completion so a blocking readBoundedLine
    // parked on the socket is released instead of leaking the IO thread.
    val job = currentCoroutineContext()[Job]
    val handle = job?.invokeOnCompletion {
        try { stream.close() } catch (_: Exception) {}
    }

    try {
        while (true) {
            val line = readBoundedLine(stream) ?: break

            if (line.isEmpty()) {
                // Blank line = dispatch event
                val data = currentData
                if (data != null) {
                    emit(
                        SseFrame(
                            data = data.toString(),
                            event = currentEvent,
                            id = currentId,
                            retry = currentRetry,
                        )
                    )
                }
                currentData = null
                frameBytes = 0
                currentEvent = null
                currentRetry = null
                // Note: currentId persists across frames (Last-Event-ID)
                continue
            }

            val colonIdx = line.indexOf(':')
            if (colonIdx == -1) {
                // Line is a field name with no value
                continue
            }

            val field = line.substring(0, colonIdx)
            val rawValue = line.substring(colonIdx + 1)
            val value = if (rawValue.startsWith(" ")) rawValue.substring(1) else rawValue

            when (field) {
                "data" -> {
                    frameBytes += value.length + 1 // +1 for the joining newline
                    if (frameBytes > MAX_SSE_FRAME_BYTES) {
                        throw IOException(
                            "SSE frame exceeds maximum allowed $MAX_SSE_FRAME_BYTES bytes"
                        )
                    }
                    if (currentData == null) {
                        currentData = StringBuilder(value)
                    } else {
                        currentData.append('\n').append(value)
                    }
                }
                "event" -> currentEvent = value
                "id" -> currentId = value
                "retry" -> currentRetry = value.toLongOrNull()
                "" -> { /* comment, ignore */ }
            }
        }
    } finally {
        handle?.dispose()
    }
}.flowOn(Dispatchers.IO)

// ---------------------------------------------------------------------------
// Reconnecting SSE stream
// ---------------------------------------------------------------------------

data class SseStreamOptions(
    val initialDelayMs: Long = 1_000L,
    val maxDelayMs: Long = 30_000L,
)

/**
 * Reconnecting SSE stream as a Kotlin [Flow].
 *
 * @param connect Called on each (re)connection attempt. Receives the last
 *   event ID for the Last-Event-ID header. Must return an InputStream from
 *   which SSE bytes are read.
 * @param opts Reconnect policy configuration.
 */
fun sseStream(
    connect: suspend (lastEventId: String?) -> InputStream,
    opts: SseStreamOptions = SseStreamOptions(),
): Flow<SseFrame> = flow {
    var delayMs = opts.initialDelayMs
    var lastEventId: String? = null

    while (true) {
        val stream: InputStream = try {
            connect(lastEventId)
        } catch (e: OrbApiError) {
            // 4xx errors are terminal — do not reconnect (no frame-parse, no retry).
            // 5xx is a candidate for reconnect with back-off.
            if (e.statusCode in 400..499) throw e
            delay(jitter(delayMs))
            delayMs = min(delayMs * 2, opts.maxDelayMs)
            continue
        } catch (e: Exception) {
            if (e is CancellationException) throw e
            delay(jitter(delayMs))
            delayMs = min(delayMs * 2, opts.maxDelayMs)
            continue
        }

        var gotEvents = false
        try {
            parseSseFrames(stream).collect { frame ->
                if (frame.id != null) lastEventId = frame.id
                if (frame.retry != null) delayMs = frame.retry
                gotEvents = true
                emit(frame)
                if (frame.isSentinel()) {
                    // Terminal sentinel — stop the flow cleanly
                    throw TerminalSentinelException()
                }
            }
        } catch (e: TerminalSentinelException) {
            return@flow
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // Stream error — reconnect below
        } finally {
            try { stream.close() } catch (_: Exception) {}
        }

        // Clean stream end or error — reconnect with back-off
        if (gotEvents) {
            delayMs = opts.initialDelayMs // reset after successful connection
        }

        delay(jitter(delayMs))
        delayMs = min(delayMs * 2, opts.maxDelayMs)
    }
}

private fun jitter(delayMs: Long): Long {
    return (delayMs * (0.5 + Math.random() * 0.5)).toLong()
}

/** Thrown internally to terminate the reconnect loop on sentinel. */
internal class TerminalSentinelException : Exception()
