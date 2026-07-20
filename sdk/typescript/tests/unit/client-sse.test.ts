/**
 * Unit tests for OrbClient SSE (re)connection resource handling.
 *
 * Regression coverage for the leak where connect() threw on an HTTP error
 * status WITHOUT destroying the already-opened response stream, leaking the
 * underlying socket/PassThrough.
 */
import { PassThrough } from "stream";
import { OrbClient } from "../../src/client";

/**
 * Build a mock axios adapter that resolves an SSE-style response carrying a
 * live PassThrough as `data`, at the given HTTP status. Mirrors how the real
 * instance behaves under `validateStatus: () => true` (the response resolves
 * even for 4xx/5xx). Returns the stream so the test can assert on its state.
 */
function makeStreamAdapter(status: number) {
  const stream = new PassThrough();
  return {
    stream,
    adapter: async (config: any) => ({
      status,
      statusText: String(status),
      data: stream,
      headers: {},
      config,
      request: {},
    }),
  };
}

/** Swap in a mock adapter on the client's internal axios instance. */
function setAdapter(client: OrbClient, adapter: any): void {
  (client as any).http.defaults.adapter = adapter;
}

describe("OrbClient SSE connect() resource handling", () => {
  it("destroys the response stream when streamRequestStatus connect gets an error status", async () => {
    const client = await OrbClient.create({ baseUrl: "http://test.invalid" });
    const { stream, adapter } = makeStreamAdapter(404); // 4xx => surfaced, no reconnect
    setAdapter(client, adapter);

    expect(stream.destroyed).toBe(false);

    const iter = client.streamRequestStatus("req-123");
    await expect(iter.next()).rejects.toThrow(/HTTP 404/);

    // The opened stream must have been drained/destroyed before throwing.
    expect(stream.destroyed).toBe(true);
  }, 10_000);

  it("destroys the response stream when streamEvents connect gets an error status", async () => {
    const client = await OrbClient.create({ baseUrl: "http://test.invalid" });
    const { stream, adapter } = makeStreamAdapter(404);
    setAdapter(client, adapter);

    expect(stream.destroyed).toBe(false);

    const iter = client.streamEvents();
    await expect(iter.next()).rejects.toThrow(/HTTP 404/);

    expect(stream.destroyed).toBe(true);
  }, 10_000);
});
