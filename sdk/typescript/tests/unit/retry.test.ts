/**
 * Unit tests for the retry transport (Layer 3).
 */
import axios, { AxiosError } from "axios";
import { attachRetry, disableRetry } from "../../src/transport/retry";

/** Build an adapter that fails N times then succeeds */
function makeAdapter(failCount: number, failStatus: number) {
  let callCount = 0;
  return {
    count: () => callCount,
    adapter: async (config: any) => {
      callCount++;
      if (callCount <= failCount) {
        const response = {
          status: failStatus,
          statusText: String(failStatus),
          data: {},
          headers: {},
          config,
          request: {},
        };
        throw new AxiosError(
          `HTTP ${failStatus}`,
          `ERR_${failStatus}`,
          config,
          {},
          response
        );
      }
      return { status: 200, statusText: "OK", data: { ok: true }, headers: {}, config, request: {} };
    },
  };
}

/** Build an adapter that raises a network-level AxiosError (no response) */
function makeNetworkAdapter(failCount: number, code: string) {
  let callCount = 0;
  return {
    count: () => callCount,
    adapter: async (config: any) => {
      callCount++;
      if (callCount <= failCount) {
        // Network error: AxiosError with a code and NO response.
        throw new AxiosError(`network ${code}`, code, config, {});
      }
      return { status: 200, statusText: "OK", data: { ok: true }, headers: {}, config, request: {} };
    },
  };
}

describe("retry transport", () => {
  it("does not retry on 200", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 2, baseDelayMs: 1 });

    let callCount = 0;
    instance.defaults.adapter = async (config: any) => {
      callCount++;
      return { status: 200, statusText: "OK", data: { ok: true }, headers: {}, config, request: {} };
    };

    const resp = await instance.get("/test");
    expect(resp.status).toBe(200);
    expect(callCount).toBe(1);
  }, 10_000);

  it("retries on 503 for GET (success on 3rd attempt)", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 2, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(2, 503);
    instance.defaults.adapter = adapter as any;

    const resp = await instance.get("/test");
    expect(resp.status).toBe(200);
    expect(count()).toBe(3);
  }, 10_000);

  it("does NOT retry on 404", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(99, 404);
    instance.defaults.adapter = adapter as any;

    await expect(instance.get("/test")).rejects.toThrow();
    expect(count()).toBe(1); // no retry on 4xx
  }, 10_000);

  it("does NOT retry POST on 500", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(99, 500);
    instance.defaults.adapter = adapter as any;

    // POST is non-idempotent — should NOT retry on 5xx
    await expect(instance.post("/test", {})).rejects.toThrow();
    expect(count()).toBe(1);
  }, 10_000);

  it("retries GET on 500", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 1, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(99, 500);
    instance.defaults.adapter = adapter as any;

    await expect(instance.get("/test")).rejects.toThrow();
    expect(count()).toBe(2); // GET + 1 retry
  }, 10_000);

  it("does NOT retry POST on 429 (non-idempotent — may double-provision)", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(99, 429);
    instance.defaults.adapter = adapter as any;

    // A POST that got 429 may already have been processed server-side; never
    // auto-retry it.
    await expect(instance.post("/test", {})).rejects.toThrow();
    expect(count()).toBe(1);
  }, 10_000);

  it("does NOT retry POST on 503 (non-idempotent)", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(99, 503);
    instance.defaults.adapter = adapter as any;

    await expect(instance.post("/test", {})).rejects.toThrow();
    expect(count()).toBe(1);
  }, 10_000);

  it("retries on 429 for GET (idempotent)", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 1, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(1, 429);
    instance.defaults.adapter = adapter as any;

    const resp = await instance.get("/test");
    expect(resp.status).toBe(200);
    expect(count()).toBe(2);
  }, 10_000);

  it("retries PUT on 503 (idempotent)", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 1, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(1, 503);
    instance.defaults.adapter = adapter as any;

    const resp = await instance.put("/test", {});
    expect(resp.status).toBe(200);
    expect(count()).toBe(2);
  }, 10_000);

  it("exhausts retries and throws final error", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 2, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(99, 503);
    instance.defaults.adapter = adapter as any;

    await expect(instance.get("/test")).rejects.toThrow();
    expect(count()).toBe(3); // initial + 2 retries
  }, 10_000);

  it("retries GET on a network error", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 2, baseDelayMs: 1 });

    const { adapter, count } = makeNetworkAdapter(1, "ECONNRESET");
    instance.defaults.adapter = adapter as any;

    const resp = await instance.get("/test");
    expect(resp.status).toBe(200);
    expect(count()).toBe(2);
  }, 10_000);

  it("does NOT retry POST on a post-write network error (ECONNRESET)", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeNetworkAdapter(99, "ECONNRESET");
    instance.defaults.adapter = adapter as any;

    // The request may have reached the server before the socket dropped.
    await expect(instance.post("/test", {})).rejects.toThrow();
    expect(count()).toBe(1);
  }, 10_000);

  it("DOES retry POST on a pre-write connection refusal (ECONNREFUSED)", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 2, baseDelayMs: 1 });

    const { adapter, count } = makeNetworkAdapter(1, "ECONNREFUSED");
    instance.defaults.adapter = adapter as any;

    // Connection refused => server never received the request => safe to retry.
    const resp = await instance.post("/test", {});
    expect(resp.status).toBe(200);
    expect(count()).toBe(2);
  }, 10_000);

  it("does NOT retry a request marked with disableRetry()", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeAdapter(99, 503);
    instance.defaults.adapter = adapter as any;

    // GET on 503 would normally retry; disableRetry opts out.
    await expect(instance.get("/test", disableRetry({}))).rejects.toThrow();
    expect(count()).toBe(1);
  }, 10_000);
});

/**
 * These tests mirror the REAL client configuration: the OrbClient axios
 * instance is created with `validateStatus: () => true`, so axios RESOLVES
 * every response — including 429/503/5xx — instead of rejecting. The adapters
 * here therefore RETURN error responses (they do not throw), exactly like a
 * real HTTP round-trip against a server returning 503.
 *
 * The original suite above used bare instances + throwing adapters, so it never
 * exercised this path and the status-based retry was silently dead code.
 */
describe("retry transport — permissive validateStatus (real-client path)", () => {
  /** Adapter that RESOLVES an error response N times then RESOLVES a 200. */
  function makeResolvingAdapter(failCount: number, failStatus: number) {
    let callCount = 0;
    return {
      count: () => callCount,
      adapter: async (config: any) => {
        callCount++;
        if (callCount <= failCount) {
          return {
            status: failStatus,
            statusText: String(failStatus),
            data: {},
            headers: {},
            config,
            request: {},
          };
        }
        return { status: 200, statusText: "OK", data: { ok: true }, headers: {}, config, request: {} };
      },
    };
  }

  it("retries a 503 on an idempotent GET the expected number of times, then succeeds", async () => {
    const instance = axios.create({
      baseURL: "http://test.invalid",
      validateStatus: () => true, // exactly what OrbClient.create() sets
    });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeResolvingAdapter(2, 503);
    instance.defaults.adapter = adapter as any;

    const resp = await instance.get("/test");
    expect(resp.status).toBe(200); // eventually recovers
    expect(count()).toBe(3); // initial 503 + 503 + success
  }, 10_000);

  it("exhausts retries on a persistent 503 GET and RESOLVES the final 503 (caller throws)", async () => {
    const instance = axios.create({
      baseURL: "http://test.invalid",
      validateStatus: () => true,
    });
    attachRetry(instance, { maxRetries: 2, baseDelayMs: 1 });

    const { adapter, count } = makeResolvingAdapter(99, 503);
    instance.defaults.adapter = adapter as any;

    // With permissive validateStatus the exhausted response resolves (it does
    // not throw); the client's own `resp.status >= 400` check turns it into an
    // error. Here we assert the retry budget was spent and the status is 503.
    const resp = await instance.get("/test");
    expect(resp.status).toBe(503);
    expect(count()).toBe(3); // initial + 2 retries
  }, 10_000);

  it("does NOT retry a 503 on a non-idempotent POST (may double-provision)", async () => {
    const instance = axios.create({
      baseURL: "http://test.invalid",
      validateStatus: () => true,
    });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeResolvingAdapter(99, 503);
    instance.defaults.adapter = adapter as any;

    const resp = await instance.post("/test", {});
    expect(resp.status).toBe(503);
    expect(count()).toBe(1); // no retry for POST
  }, 10_000);

  it("does NOT retry a 404 GET (terminal client error)", async () => {
    const instance = axios.create({
      baseURL: "http://test.invalid",
      validateStatus: () => true,
    });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeResolvingAdapter(99, 404);
    instance.defaults.adapter = adapter as any;

    const resp = await instance.get("/test");
    expect(resp.status).toBe(404);
    expect(count()).toBe(1);
  }, 10_000);

  it("does NOT retry a 503 GET marked with disableRetry() (health() path)", async () => {
    const instance = axios.create({
      baseURL: "http://test.invalid",
      validateStatus: () => true,
    });
    attachRetry(instance, { maxRetries: 3, baseDelayMs: 1 });

    const { adapter, count } = makeResolvingAdapter(99, 503);
    instance.defaults.adapter = adapter as any;

    const resp = await instance.get("/test", disableRetry({}));
    expect(resp.status).toBe(503);
    expect(count()).toBe(1);
  }, 10_000);

  it("does not retry a 200 (fulfillment interceptor passes it through untouched)", async () => {
    const instance = axios.create({
      baseURL: "http://test.invalid",
      validateStatus: () => true,
    });
    attachRetry(instance, { maxRetries: 2, baseDelayMs: 1 });

    const { adapter, count } = makeResolvingAdapter(0, 503);
    instance.defaults.adapter = adapter as any;

    const resp = await instance.get("/test");
    expect(resp.status).toBe(200);
    expect(count()).toBe(1);
  }, 10_000);
});
