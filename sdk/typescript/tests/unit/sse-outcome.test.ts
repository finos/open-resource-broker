/**
 * Request status stream outcome frames: timeout and error must surface as
 * errors; complete frames, plain data and old-server streams are unchanged.
 */
import { Readable } from "stream";
import { sseStream, parseOrbPayload, isSentinel, TERMINAL_STATUSES } from "../../src/sse/reader";
import { throwOnStreamOutcome } from "../../src/sse/outcome";
import { OrbError, OrbStreamError, OrbStreamTimeoutError } from "../../src/errors";

const inProgress = 'data: {"requests":[{"request_id":"r1","status":"in_progress"}]}\n\n';
const failed = 'data: {"requests":[{"request_id":"r1","status":"failed"}]}\n\n';

/** Consume a stream exactly as OrbClient.streamRequestStatus does. */
async function consume(wire: string): Promise<string[]> {
  const statuses: string[] = [];
  const connect = async () => Readable.from([Buffer.from(wire, "utf8")]);
  for await (const frame of sseStream(connect, {})) {
    throwOnStreamOutcome(frame);
    if (isSentinel(frame)) return statuses;
    const payload = parseOrbPayload(frame);
    for (const req of payload?.requests ?? []) {
      statuses.push(req.status);
      if (TERMINAL_STATUSES.has(req.status)) return statuses;
    }
  }
  return statuses;
}

describe("request status stream outcome frames", () => {
  it("raises a distinct timeout error on a timeout frame", async () => {
    const wire = inProgress + 'event: timeout\ndata: {"status": "timeout"}\n\ndata: {}\n\n';
    const err = await consume(wire).catch((e) => e);
    expect(err).toBeInstanceOf(OrbStreamTimeoutError);
    expect(err).toBeInstanceOf(OrbError);
    expect(err).not.toBeInstanceOf(OrbStreamError);
  }, 10_000);

  it("raises an error on an error frame", async () => {
    const wire = inProgress + 'event: error\ndata: {"error": "internal"}\n\ndata: {}\n\n';
    const err = await consume(wire).catch((e) => e);
    expect(err).toBeInstanceOf(OrbStreamError);
    expect(err).not.toBeInstanceOf(OrbStreamTimeoutError);
  }, 10_000);

  it("treats a complete frame as a normal end", async () => {
    const wire =
      inProgress + 'event: complete\ndata: {"status": "failed"}\n\n' + "data: {}\n\n";
    await expect(consume(wire)).resolves.toEqual(["in_progress"]);
  }, 10_000);

  it("keeps the final status readable when the stream completes", async () => {
    const complete = 'data: {"requests":[{"request_id":"r1","status":"complete"}]}\n\n';
    const wire =
      inProgress + complete + 'event: complete\ndata: {"status": "complete"}\n\n' + "data: {}\n\n";
    const statuses = await consume(wire);
    expect(statuses[statuses.length - 1]).toBe("complete");
  }, 10_000);

  it("behaves as before for servers without named frames", async () => {
    await expect(consume(inProgress + failed + "data: {}\n\n")).resolves.toEqual([
      "in_progress",
      "failed",
    ]);
    await expect(consume(inProgress + "data: {}\n\n")).resolves.toEqual(["in_progress"]);
  }, 10_000);
});
