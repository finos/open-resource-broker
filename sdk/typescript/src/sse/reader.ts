/**
 * Layer 5: Server-Sent Events Reader + Reconnect
 *
 * Parses SSE wire format from a Node.js readable stream, yields typed events,
 * and reconnects with exponential back-off when the connection is dropped.
 *
 * ORB SSE wire format:
 *   data: <json>\n\n   — normal event
 *   data: {}\n\n       — terminal sentinel (stream done)
 *
 * Auth headers are applied by the Axios interceptor on each (re)connection.
 */

import { Readable } from "stream";

// ---------------------------------------------------------------------------
// Wire-level SSE frame types
// ---------------------------------------------------------------------------

export interface SseFrame {
  data: string;
  event?: string;
  id?: string;
  retry?: number;
}

/**
 * Maximum size of a single unparsed SSE frame/line buffer, mirroring the Go
 * SDK's 4 MiB cap. A server that never emits a newline or emits an enormous
 * data line would otherwise grow client memory without bound. When exceeded we
 * abort the stream with a non-retryable typed error so the reconnect loop does
 * not spin on the same oversized frame.
 */
export const MAX_SSE_FRAME_BYTES = 4 * 1024 * 1024;

/**
 * Thrown when an SSE frame exceeds MAX_SSE_FRAME_BYTES. Non-retryable: the
 * reconnect loop must surface this rather than reconnect.
 */
export class SseFrameTooLargeError extends Error {
  constructor(limit: number) {
    super(`orb: SSE frame exceeded maximum size of ${limit} bytes`);
    this.name = "SseFrameTooLargeError";
  }
}

// ---------------------------------------------------------------------------
// ORB-specific payload types (mirrors sdk/go/internal/sse/reader.go)
// ---------------------------------------------------------------------------

export interface OrbMachine {
  machine_id: string;
  name?: string;
  status?: string;
  result?: string;
  private_ip?: string;
  public_ip?: string;
  launch_time?: string;
  message?: string;
}

export interface OrbSseRequest {
  request_id: string;
  status: string;
  message?: string;
  requested_count?: number;
  successful_count?: number;
  failed_count?: number;
  machines?: OrbMachine[];
}

export interface OrbSsePayload {
  requests?: OrbSseRequest[];
  // Global event bus shape (stream_events)
  event_type?: string;
  [key: string]: unknown;
}

export const TERMINAL_STATUSES = new Set([
  "complete",
  "completed",
  "failed",
  "error",
  "cancelled",
  "canceled",
  "partial",
  "timeout",
]);

/**
 * Returns true if the SSE frame is the ORB terminal sentinel (data: {}).
 */
export function isSentinel(frame: SseFrame): boolean {
  return frame.data.trim() === "{}";
}

/**
 * Parse an ORB SSE data frame into a typed payload.
 * Returns null if the frame is the sentinel or cannot be parsed.
 */
export function parseOrbPayload(frame: SseFrame): OrbSsePayload | null {
  if (isSentinel(frame)) return null;
  try {
    return JSON.parse(frame.data) as OrbSsePayload;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Low-level: parse SSE frames from a Node.js Readable stream
// ---------------------------------------------------------------------------

async function* parseFrames(stream: Readable): AsyncGenerator<SseFrame> {
  let buffer = "";
  let currentFrame: Partial<SseFrame> = {};

  for await (const chunk of stream) {
    buffer += typeof chunk === "string" ? chunk : chunk.toString("utf8");

    // Bound the unterminated-line buffer: a server that never emits a newline
    // would otherwise grow the heap without limit. Abort with a non-retryable
    // typed error (see MAX_SSE_FRAME_BYTES).
    if (buffer.length > MAX_SSE_FRAME_BYTES) {
      throw new SseFrameTooLargeError(MAX_SSE_FRAME_BYTES);
    }

    let newlineIdx: number;
    while ((newlineIdx = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, newlineIdx).replace(/\r$/, "");
      buffer = buffer.slice(newlineIdx + 1);

      if (line === "") {
        // Blank line = dispatch event
        if (currentFrame.data !== undefined) {
          yield currentFrame as SseFrame;
        }
        currentFrame = {};
        continue;
      }

      const colonIdx = line.indexOf(":");
      if (colonIdx === -1) {
        // Field with no value
        currentFrame.data = currentFrame.data ?? "";
        continue;
      }

      const field = line.slice(0, colonIdx);
      const value = line.slice(colonIdx + 1).replace(/^ /, "");

      switch (field) {
        case "data":
          currentFrame.data =
            currentFrame.data !== undefined
              ? `${currentFrame.data}\n${value}`
              : value;
          // Bound the accumulated multi-line data field too.
          if (currentFrame.data.length > MAX_SSE_FRAME_BYTES) {
            throw new SseFrameTooLargeError(MAX_SSE_FRAME_BYTES);
          }
          break;
        case "event":
          currentFrame.event = value;
          break;
        case "id":
          currentFrame.id = value;
          break;
        case "retry":
          currentFrame.retry = Number(value);
          break;
        // Ignore comment lines (field === "")
      }
    }
  }
}

// ---------------------------------------------------------------------------
// High-level: AsyncGenerator that reconnects on drop
// ---------------------------------------------------------------------------

export interface SseStreamOptions {
  /** Signal to abort the stream */
  signal?: AbortSignal;
  /** Initial back-off delay in ms (default 1000) */
  initialDelayMs?: number;
  /** Maximum back-off delay in ms (default 30_000) */
  maxDelayMs?: number;
  /** Last-Event-ID to send on reconnect */
  lastEventId?: string;
}

/**
 * Reconnecting SSE stream as an AsyncGenerator.
 *
 * @param connect - Called on each (re)connection attempt. Must return a
 *   Node.js Readable that emits SSE wire-format bytes. Receives the
 *   Last-Event-ID string if one is available (for the Last-Event-ID header).
 */
export async function* sseStream(
  connect: (lastEventId?: string) => Promise<Readable>,
  opts: SseStreamOptions = {}
): AsyncGenerator<SseFrame> {
  const { signal } = opts;
  let delayMs = opts.initialDelayMs ?? 1_000;
  const maxDelayMs = opts.maxDelayMs ?? 30_000;
  let lastEventId = opts.lastEventId;

  while (!signal?.aborted) {
    let stream: Readable;
    try {
      stream = await connect(lastEventId);
    } catch (err) {
      if (signal?.aborted) return;
      // Don't retry on 4xx errors — these are client errors, not transient failures
      if (err instanceof Error && "statusCode" in err) {
        const status = (err as { statusCode: number }).statusCode;
        if (status >= 400 && status < 500) throw err;
      }
      await sleep(jitter(delayMs));
      delayMs = Math.min(delayMs * 2, maxDelayMs);
      continue;
    }

    let gotEvents = false;
    try {
      for await (const frame of parseFrames(stream)) {
        if (signal?.aborted) return;

        if (frame.id !== undefined) {
          lastEventId = frame.id;
        }
        if (frame.retry !== undefined) {
          delayMs = frame.retry;
        }

        gotEvents = true;
        yield frame;

        if (isSentinel(frame)) return; // terminal sentinel — no reconnect
      }
    } catch (err) {
      // An oversized frame is a non-retryable error: surface it (the finally
      // block destroys the stream) rather than reconnecting onto the same
      // misbehaving stream.
      if (err instanceof SseFrameTooLargeError) throw err;
      // Other stream errors — fall through to reconnect.
    } finally {
      if (typeof stream.destroy === "function") {
        stream.destroy();
      }
    }

    if (signal?.aborted) return;

    if (gotEvents) {
      delayMs = opts.initialDelayMs ?? 1_000; // reset after successful connection
    }

    await sleep(jitter(delayMs));
    delayMs = Math.min(delayMs * 2, maxDelayMs);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jitter(ms: number): number {
  return ms * (0.5 + Math.random() * 0.5);
}
