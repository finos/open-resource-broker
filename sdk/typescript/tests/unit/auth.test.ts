/**
 * Unit tests for the auth module (Layer 4).
 * Verifies that auth headers are applied correctly to requests.
 */
import axios from "axios";
import { attachAuth, type AuthOption } from "../../src/auth/index";

describe("auth: none", () => {
  it("does not add Authorization header", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachAuth(instance, { type: "none" });

    let capturedHeaders: Record<string, string> = {};
    instance.defaults.adapter = async (config: any) => {
      capturedHeaders = Object.fromEntries(
        Object.entries(config.headers ?? {}).map(([k, v]) => [k.toLowerCase(), String(v)])
      );
      return {
        status: 200,
        statusText: "OK",
        data: {},
        headers: {},
        config,
        request: {},
      };
    };

    await instance.get("/test");
    expect(capturedHeaders["authorization"]).toBeUndefined();
  });
});

describe("auth: bearer (static token)", () => {
  it("adds Authorization: Bearer <token>", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    const auth: AuthOption = { type: "bearer", token: "my-secret-token" };
    attachAuth(instance, auth);

    let capturedHeaders: Record<string, string> = {};
    instance.defaults.adapter = async (config: any) => {
      capturedHeaders = {};
      for (const [k, v] of Object.entries(config.headers ?? {})) {
        if (typeof v === "string" || typeof v === "number") {
          capturedHeaders[String(k).toLowerCase()] = String(v);
        }
      }
      return {
        status: 200,
        statusText: "OK",
        data: {},
        headers: {},
        config,
        request: {},
      };
    };

    await instance.get("/test");
    expect(capturedHeaders["authorization"]).toBe("Bearer my-secret-token");
  });

  it("adds Authorization: Bearer from dynamic function", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    const auth: AuthOption = {
      type: "bearer",
      token: async () => "dynamic-token",
    };
    attachAuth(instance, auth);

    let authHeader = "";
    instance.defaults.adapter = async (config: any) => {
      for (const [k, v] of Object.entries(config.headers ?? {})) {
        if (String(k).toLowerCase() === "authorization") {
          authHeader = String(v);
        }
      }
      return {
        status: 200,
        statusText: "OK",
        data: {},
        headers: {},
        config,
        request: {},
      };
    };

    await instance.get("/test");
    expect(authHeader).toBe("Bearer dynamic-token");
  });
});

describe("auth: bearer applied to all requests", () => {
  it("applies to POST requests too", async () => {
    const instance = axios.create({ baseURL: "http://test.invalid" });
    attachAuth(instance, { type: "bearer", token: "tok" });

    let authHeader = "";
    instance.defaults.adapter = async (config: any) => {
      for (const [k, v] of Object.entries(config.headers ?? {})) {
        if (String(k).toLowerCase() === "authorization") authHeader = String(v);
      }
      return { status: 200, statusText: "OK", data: {}, headers: {}, config, request: {} };
    };

    await instance.post("/test", { key: "value" });
    expect(authHeader).toBe("Bearer tok");
  });
});
