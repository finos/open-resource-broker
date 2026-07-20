/**
 * Unit tests for the SSE reader (Layer 5).
 */
import { Readable } from "stream";
import {
  isSentinel,
  parseOrbPayload,
  sseStream,
  TERMINAL_STATUSES,
  MAX_SSE_FRAME_BYTES,
  SseFrameTooLargeError,
  type SseFrame,
} from "../../src/sse/reader";

function makeReadable(text: string): Readable {
  return Readable.from([Buffer.from(text, "utf8")]);
}

/** Collect all frames from an async generator with a timeout guard */
async function collectFrames(
  gen: AsyncGenerator<SseFrame>,
  timeoutMs = 2000
): Promise<SseFrame[]> {
  const frames: SseFrame[] = [];
  const deadline = Date.now() + timeoutMs;
  for await (const frame of gen) {
    frames.push(frame);
    if (Date.now() > deadline) break;
  }
  return frames;
}

describe("isSentinel", () => {
  it("returns true for {}", () => {
    expect(isSentinel({ data: "{}" })).toBe(true);
    expect(isSentinel({ data: "  {}  " })).toBe(true);
  });

  it("returns false for non-empty data", () => {
    expect(isSentinel({ data: '{"requests":[]}' })).toBe(false);
    expect(isSentinel({ data: "hello" })).toBe(false);
  });
});

describe("parseOrbPayload", () => {
  it("returns null for sentinel", () => {
    expect(parseOrbPayload({ data: "{}" })).toBeNull();
  });

  it("parses a valid payload", () => {
    const frame: SseFrame = {
      data: JSON.stringify({
        requests: [
          {
            request_id: "req-1",
            status: "pending",
            machines: [],
          },
        ],
      }),
    };
    const payload = parseOrbPayload(frame);
    expect(payload).not.toBeNull();
    expect(payload?.requests?.[0].request_id).toBe("req-1");
    expect(payload?.requests?.[0].status).toBe("pending");
  });

  it("returns null for invalid JSON", () => {
    expect(parseOrbPayload({ data: "not json" })).toBeNull();
  });
});

describe("TERMINAL_STATUSES", () => {
  it("contains expected statuses", () => {
    expect(TERMINAL_STATUSES.has("complete")).toBe(true);
    expect(TERMINAL_STATUSES.has("completed")).toBe(true);
    expect(TERMINAL_STATUSES.has("failed")).toBe(true);
    expect(TERMINAL_STATUSES.has("error")).toBe(true);
    expect(TERMINAL_STATUSES.has("cancelled")).toBe(true);
    expect(TERMINAL_STATUSES.has("partial")).toBe(true);
    expect(TERMINAL_STATUSES.has("timeout")).toBe(true);
  });

  it("does not contain pending", () => {
    expect(TERMINAL_STATUSES.has("pending")).toBe(false);
    expect(TERMINAL_STATUSES.has("running")).toBe(false);
  });
});

describe("sseStream (AsyncGenerator)", () => {
  it("yields frames from a stream ending with sentinel", async () => {
    // End with sentinel so the generator terminates without reconnect
    const sseText = "data: hello\n\ndata: {}\n\n";
    const ac = new AbortController();

    async function connect(): Promise<Readable> {
      return makeReadable(sseText);
    }

    const gen = sseStream(connect, { signal: ac.signal });
    const frames = await collectFrames(gen);

    // Should get "hello" and then stop (sentinel terminates without yielding)
    expect(frames).toHaveLength(2);
    expect(frames[0].data).toBe("hello");
    expect(isSentinel(frames[1])).toBe(true);
  }, 10_000);

  it("stops on sentinel {} without yielding post-sentinel frames", async () => {
    // The sseStream itself stops at sentinel — verify this
    const sseText =
      'data: {"requests":[{"request_id":"r1","status":"pending"}]}\n\n' +
      "data: {}\n\n" +
      'data: {"requests":[{"request_id":"r1","status":"complete"}]}\n\n';

    const ac = new AbortController();
    async function connect(): Promise<Readable> {
      return makeReadable(sseText);
    }

    const gen = sseStream(connect, { signal: ac.signal });
    const frames = await collectFrames(gen);

    // Should get first data frame and the sentinel; NOT the third frame
    expect(frames).toHaveLength(2);
    expect(isSentinel(frames[1])).toBe(true);
  }, 10_000);

  it("respects AbortSignal", async () => {
    const ac = new AbortController();
    // Abort before we even start
    ac.abort();

    let connectCalled = false;
    async function connect(): Promise<Readable> {
      connectCalled = true;
      return makeReadable("data: hi\n\ndata: {}\n\n");
    }

    const frames: SseFrame[] = [];
    for await (const frame of sseStream(connect, { signal: ac.signal })) {
      frames.push(frame);
    }

    // Already aborted — should yield nothing
    expect(frames).toHaveLength(0);
  }, 5_000);

  it("parses event: and id: fields", async () => {
    const sseText = "event: update\nid: 42\ndata: payload\n\ndata: {}\n\n";
    const ac = new AbortController();

    async function connect(): Promise<Readable> {
      return makeReadable(sseText);
    }

    const gen = sseStream(connect, { signal: ac.signal });
    const frames = await collectFrames(gen);

    expect(frames[0].event).toBe("update");
    expect(frames[0].id).toBe("42");
    expect(frames[0].data).toBe("payload");
  }, 10_000);

  it("handles multi-line data fields", async () => {
    const sseText = "data: line1\ndata: line2\n\ndata: {}\n\n";
    const ac = new AbortController();

    async function connect(): Promise<Readable> {
      return makeReadable(sseText);
    }

    const gen = sseStream(connect, { signal: ac.signal });
    const frames = await collectFrames(gen);

    expect(frames[0].data).toBe("line1\nline2");
  }, 10_000);

  it("aborts with a typed error on an oversized never-terminated frame (no reconnect)", async () => {
    // A data line larger than the cap, never followed by a blank line.
    const huge = "data: " + "x".repeat(MAX_SSE_FRAME_BYTES + 1024);
    const ac = new AbortController();

    let connectCount = 0;
    async function connect(): Promise<Readable> {
      connectCount++;
      return makeReadable(huge);
    }

    let caught: unknown;
    try {
      for await (const _frame of sseStream(connect, {
        signal: ac.signal,
        initialDelayMs: 1,
      })) {
        // no-op
      }
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeInstanceOf(SseFrameTooLargeError);
    // Non-retryable: it must NOT reconnect onto the same oversized stream.
    expect(connectCount).toBe(1);
  }, 10_000);
});
