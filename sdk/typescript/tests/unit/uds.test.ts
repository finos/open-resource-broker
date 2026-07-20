/**
 * Unit tests for the UDS adapter (Layer 2).
 *
 * These tests verify the adapter's interface contract without an actual socket.
 * A real end-to-end UDS test is in tests/contract/contract.test.ts.
 */
import { makeUdsAdapter } from "../../src/transport/uds";

describe("makeUdsAdapter", () => {
  it("returns a function (Axios adapter)", () => {
    const adapter = makeUdsAdapter("/tmp/test.sock");
    expect(typeof adapter).toBe("function");
  });

  it("throws on connection to non-existent socket", async () => {
    const adapter = makeUdsAdapter("/tmp/nonexistent-orb-test.sock");

    const config: any = {
      url: "http://localhost/health",
      method: "get",
      headers: { accept: "application/json" },
      validateStatus: () => true,
    };

    await expect(adapter(config)).rejects.toThrow();
  });

  it("adapter rejects with error for unreachable socket, not silently fails", async () => {
    const adapter = makeUdsAdapter("/tmp/orb-unit-test-definitely-does-not-exist.sock");

    const config: any = {
      url: "/health",
      method: "get",
      headers: {},
      validateStatus: () => true,
    };

    let threw = false;
    try {
      await adapter(config);
    } catch {
      threw = true;
    }
    expect(threw).toBe(true);
  });
});
