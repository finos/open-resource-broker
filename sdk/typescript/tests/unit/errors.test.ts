/**
 * Unit tests for the error handling module.
 */
import axios from "axios";
import {
  parseApiError,
  apiErrorForStatus,
  OrbError,
  OrbApiError,
  OrbUnauthorizedError,
  OrbForbiddenError,
  OrbNotFoundError,
  OrbConflictError,
  OrbTimeoutError,
  OrbUnavailableError,
} from "../../src/errors";

describe("parseApiError", () => {
  it("passes through OrbApiError unchanged", () => {
    const err = new OrbApiError({ statusCode: 404, message: "not found" });
    const result = parseApiError(err);
    expect(result).toBe(err);
  });

  it("converts Axios 404 to OrbApiError with correct status", () => {
    const axiosErr = new axios.AxiosError(
      "Request failed with status code 404",
      undefined,
      {} as any,
      {},
      {
        status: 404,
        statusText: "Not Found",
        data: { error: { code: "RESOURCE_NOT_FOUND", message: "Template not found" } },
        headers: {},
        config: {} as any,
        request: {},
      }
    );

    const result = parseApiError(axiosErr);
    expect(result).toBeInstanceOf(OrbApiError);
    // parseApiError constructs the typed sentinel subclass for 404.
    expect(result).toBeInstanceOf(OrbNotFoundError);
    expect(result.statusCode).toBe(404);
    expect(result.isNotFound).toBe(true);
    expect(result.message).toBe("Template not found");
    expect(result.code).toBe("RESOURCE_NOT_FOUND");
  });

  it("extracts the request ID from response headers", () => {
    const axiosErr = new axios.AxiosError(
      "Request failed with status code 500",
      undefined,
      {} as any,
      {},
      {
        status: 500,
        statusText: "Internal Server Error",
        data: { error: { code: "INTERNAL", message: "boom" } },
        headers: { "X-Request-ID": "req-abc-123" },
        config: {} as any,
        request: {},
      }
    );

    const result = parseApiError(axiosErr);
    expect(result.requestId).toBe("req-abc-123");
    expect(result.code).toBe("INTERNAL");
  });

  it("constructs the correct typed subclass per status", () => {
    const axiosErrFor = (status: number) =>
      new axios.AxiosError(`HTTP ${status}`, undefined, {} as any, {}, {
        status,
        statusText: "",
        data: { error: { message: "x" } },
        headers: {},
        config: {} as any,
        request: {},
      });
    expect(parseApiError(axiosErrFor(401))).toBeInstanceOf(OrbUnauthorizedError);
    expect(parseApiError(axiosErrFor(403))).toBeInstanceOf(OrbForbiddenError);
    expect(parseApiError(axiosErrFor(404))).toBeInstanceOf(OrbNotFoundError);
    expect(parseApiError(axiosErrFor(409))).toBeInstanceOf(OrbConflictError);
    expect(parseApiError(axiosErrFor(408))).toBeInstanceOf(OrbTimeoutError);
    expect(parseApiError(axiosErrFor(503))).toBeInstanceOf(OrbUnavailableError);
    // No typed sentinel for 500 — falls back to base OrbApiError.
    const e500 = parseApiError(axiosErrFor(500));
    expect(e500).toBeInstanceOf(OrbApiError);
    expect(e500).not.toBeInstanceOf(OrbNotFoundError);
  });

  it("converts Axios error with detail string", () => {
    const axiosErr = new axios.AxiosError(
      "Bad request",
      undefined,
      {} as any,
      {},
      {
        status: 422,
        statusText: "Unprocessable Entity",
        data: { detail: "Validation failed" },
        headers: {},
        config: {} as any,
        request: {},
      }
    );

    const result = parseApiError(axiosErr);
    expect(result.statusCode).toBe(422);
    expect(result.message).toBe("Validation failed");
  });

  it("converts generic Error", () => {
    const err = new Error("something went wrong");
    const result = parseApiError(err);
    expect(result).toBeInstanceOf(OrbApiError);
    expect(result.message).toBe("something went wrong");
  });

  it("converts ECONNREFUSED error", () => {
    const axiosErr = new axios.AxiosError(
      "connect ECONNREFUSED",
      "ECONNREFUSED",
      {} as any
    );

    const result = parseApiError(axiosErr);
    expect(result).toBeInstanceOf(OrbApiError);
    expect(result.code).toBe("ECONNREFUSED");
  });
});

describe("OrbApiError", () => {
  it("has correct status accessors", () => {
    expect(new OrbApiError({ statusCode: 404, message: "x" }).isNotFound).toBe(true);
    expect(new OrbApiError({ statusCode: 401, message: "x" }).isUnauthorized).toBe(true);
    expect(new OrbApiError({ statusCode: 403, message: "x" }).isForbidden).toBe(true);
    expect(new OrbApiError({ statusCode: 409, message: "x" }).isConflict).toBe(true);
    expect(new OrbApiError({ statusCode: 503, message: "x" }).isUnavailable).toBe(true);
    expect(new OrbApiError({ statusCode: 408, message: "x" }).isTimeout).toBe(true);
  });
});

describe("typed sentinel subclasses", () => {
  it("are all OrbApiError / OrbError subclasses with the right status", () => {
    const cases: Array<[OrbApiError, number]> = [
      [new OrbUnauthorizedError(), 401],
      [new OrbForbiddenError(), 403],
      [new OrbNotFoundError(), 404],
      [new OrbConflictError(), 409],
      [new OrbTimeoutError(), 408],
      [new OrbUnavailableError(), 503],
    ];
    for (const [err, status] of cases) {
      expect(err).toBeInstanceOf(OrbApiError);
      expect(err).toBeInstanceOf(OrbError);
      expect(err.statusCode).toBe(status);
    }
  });

  it("OrbUnavailableError has the expected default message", () => {
    expect(new OrbUnavailableError().message).toBe("orb: service unavailable");
  });

  it("apiErrorForStatus carries code/requestId/details into the subclass", () => {
    const err = apiErrorForStatus({
      statusCode: 404,
      code: "NOT_FOUND",
      message: "missing",
      requestId: "req-9",
      details: { field: "id" },
    });
    expect(err).toBeInstanceOf(OrbNotFoundError);
    expect(err.code).toBe("NOT_FOUND");
    expect(err.requestId).toBe("req-9");
    expect(err.details).toEqual({ field: "id" });
  });
});
