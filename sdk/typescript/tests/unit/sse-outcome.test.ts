/**
 * Request status stream outcome frames: timeout and error must surface as
 * errors; complete frames, plain data and old-server streams are unchanged.
 */
import { Readable } from "stream";
import { sseStream, parseOrbPayload, isSentinel, TERMINAL_STATUSES } from "../../src/sse/reader";
import { throwOnStreamOutcome } from "../../src/sse/outcome";
import { orbSseRequestToStreamEvent, type StreamEvent } from "../../src/sse/event";
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

// Output of the server's default scheduler formatter for a request status
// stream frame: numeric launch_time, *_ip_address keys, a machine whose
// launch_time is still null.
const GOLDEN = '{"requests":[{"request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","status":"complete","template_id":"t1","requested_count":2,"created_at":"2026-10-31T01:00:00Z","machine_ids":[],"message":"done","metadata":{},"request_type":"acquire","desired_capacity":1,"successful_count":2,"failed_count":0,"error_details":{},"provider_data":{},"version":0,"resource_ids":[],"machines":[{"machine_id":"i-0abc123def4567890","name":"ip-10-0-1-12","result":"succeed","status":"running","private_ip_address":"10.0.1.12","public_ip_address":"54.12.34.56","instance_type":"t3.micro","price_type":"ondemand","cloud_host_id":"ip-10-0-1-12.ec2.internal","launch_time":1793411323,"request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","message":""},{"machine_id":"i-0def456abc7890123","name":"ip-10-0-1-13","result":"executing","status":"pending","request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","message":"","launch_time":null,"cloud_host_id":null}]}],"message":"Request status retrieved successfully","count":1}';

async function waitLike(wire: string): Promise<{ last: StreamEvent | null; error: unknown }> {
  let last: StreamEvent | null = null;
  try {
    const connect = async () => Readable.from([Buffer.from(wire, "utf8")]);
    for await (const frame of sseStream(connect, {})) {
      throwOnStreamOutcome(frame);
      if (isSentinel(frame)) break;
      for (const req of parseOrbPayload(frame)?.requests ?? []) {
        last = orbSseRequestToStreamEvent(req);
        if (TERMINAL_STATUSES.has(req.status)) return { last, error: null };
      }
    }
  } catch (error) {
    return { last, error };
  }
  return { last, error: null };
}

describe("request status stream payload with machines", () => {
  it("parses status, launch_time and IPs from the server payload", async () => {
    const { last, error } = await waitLike(
      `data: ${GOLDEN}\n\nevent: complete\ndata: {"status": "complete"}\n\ndata: {}\n\n`
    );
    expect(error).toBeNull();
    expect(last?.status).toBe("complete");
    expect(last?.machines).toHaveLength(2);
    expect(last?.machines[0]).toMatchObject({
      machineId: "i-0abc123def4567890",
      privateIp: "10.0.1.12",
      publicIp: "54.12.34.56",
      launchTime: "1793411323",
    });
    expect(last?.machines[1].launchTime).toBeUndefined();
    expect(last?.machines[1].privateIp).toBeUndefined();
  }, 10_000);

  it("keeps the last real status when the stream times out", async () => {
    const running = GOLDEN.replace('"status":"complete","template_id"', '"status":"in_progress","template_id"');
    const { last, error } = await waitLike(
      `data: ${running}\n\nevent: timeout\ndata: {"status": "timeout"}\n\ndata: {}\n\n`
    );
    expect(error).toBeInstanceOf(OrbStreamTimeoutError);
    expect(last?.status).toBe("in_progress");
    expect(last?.machines[0].launchTime).toBe("1793411323");
  }, 10_000);
});
