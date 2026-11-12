/**
 * Unit tests for the response-format header: the client names its format on every call and
 * raises SchedulerMismatchError only when the response names a different one.
 */
import { OrbClient } from "../../src/client";
import { SchedulerMismatchError } from "../../src/errors";

function adapterWith(responseHeaders: Record<string, string>, seen: Record<string, unknown>[]) {
  return async (config: any) => {
    seen.push({ ...config.headers.toJSON() });
    return {
      status: 200,
      statusText: "OK",
      data: { requests: [] },
      headers: responseHeaders,
      config,
      request: {},
    };
  };
}

async function clientWith(
  scheduler: "default" | "hostfactory" | undefined,
  responseHeaders: Record<string, string>,
  seen: Record<string, unknown>[]
): Promise<OrbClient> {
  const client = await OrbClient.create({ baseUrl: "http://test.invalid", scheduler });
  (client as any).http.defaults.adapter = adapterWith(responseHeaders, seen);
  return client;
}

describe("response format header", () => {
  it("names the default format on every call", async () => {
    const seen: Record<string, unknown>[] = [];
    const client = await clientWith(undefined, { "x-orb-scheduler": "default" }, seen);

    await client.getRequestStatus("req-1");

    expect(seen[0]["X-ORB-Scheduler"]).toBe("default");
  });

  it("does not raise when the response does not name a format", async () => {
    const client = await clientWith("hostfactory", {}, []);

    await expect(client.getRequestStatus("req-1")).resolves.toBeDefined();
  });

  it("does not raise when the response names the requested format", async () => {
    const client = await clientWith("hostfactory", { "x-orb-scheduler": "hostfactory" }, []);

    await expect(client.getRequestStatus("req-1")).resolves.toBeDefined();
  });

  it("raises SchedulerMismatchError when the response names a different format", async () => {
    const client = await clientWith("default", { "x-orb-scheduler": "hostfactory" }, []);

    const failure = client.getRequestStatus("req-1");

    await expect(failure).rejects.toBeInstanceOf(SchedulerMismatchError);
    await expect(failure).rejects.toMatchObject({ expected: "default", actual: "hostfactory" });
  });

  it("asks for the HostFactory format in the HostFactory-typed variant", async () => {
    const seen: Record<string, unknown>[] = [];
    const client = await clientWith("default", { "x-orb-scheduler": "hostfactory" }, seen);

    await client.getRequestStatusHostFactory("req-1");

    expect(seen[0]["X-ORB-Scheduler"]).toBe("hostfactory");
  });
});
