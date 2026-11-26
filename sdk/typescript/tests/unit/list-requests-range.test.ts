/**
 * listRequests forwards the creation-time range as start_time / end_time query
 * parameters and sends nothing when the range is unset.
 */
import { OrbClient } from "../../src/client";

function captureParams(client: OrbClient): Array<Record<string, unknown> | undefined> {
  const seen: Array<Record<string, unknown> | undefined> = [];
  (client as any).http.defaults.adapter = async (config: any) => {
    seen.push(config.params);
    return {
      status: 200,
      statusText: "OK",
      data: { requests: [] },
      headers: {},
      config,
      request: {},
    };
  };
  return seen;
}

describe("OrbClient.listRequests time range", () => {
  it("sends start_time and end_time", async () => {
    const client = await OrbClient.create({ baseUrl: "http://test.invalid" });
    const seen = captureParams(client);

    await client.listRequests({
      start_time: "2026-11-01T00:00:00Z",
      end_time: "2026-11-02T00:00:00.000000Z",
    });

    expect(seen[0]).toEqual({
      start_time: "2026-11-01T00:00:00Z",
      end_time: "2026-11-02T00:00:00.000000Z",
    });
  });

  it("omits the range when it is not given", async () => {
    const client = await OrbClient.create({ baseUrl: "http://test.invalid" });
    const seen = captureParams(client);

    await client.listRequests({ status: "pending" });

    expect(seen[0]).toEqual({ status: "pending" });
  });
});
