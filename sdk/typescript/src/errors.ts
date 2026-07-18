/**
 * Error types for the ORB TypeScript SDK.
 *
 * Cross-SDK contract (shared with Go/Java/Kotlin/C#):
 *   - `OrbError` is the base type for everything the SDK throws.
 *   - `OrbApiError` is thrown for all HTTP error responses and carries the
 *     canonical field set: statusCode (httpStatus), code (machine-readable
 *     errorCode, may be null), message, requestId (for support correlation).
 *   - Typed sentinel subclasses exist for 401/403/404/409/503/408 so callers
 *     can `catch (e) { if (e instanceof OrbNotFoundError) ... }`.
 */

import axios from "axios";

// ---------------------------------------------------------------------------
// Base + API error
// ---------------------------------------------------------------------------

export class OrbError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OrbError";
  }
}

export interface ApiErrorInit {
  code?: string | null;
  message: string;
  /** Server-assigned request ID (X-Request-ID) for support/correlation. */
  requestId?: string | null;
  details?: unknown;
}

export class OrbApiError extends OrbError {
  readonly statusCode: number;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly details?: unknown;

  constructor(opts: ApiErrorInit & { statusCode: number }) {
    super(opts.message);
    this.name = "OrbApiError";
    this.statusCode = opts.statusCode;
    this.code = opts.code ?? null;
    this.requestId = opts.requestId ?? null;
    this.details = opts.details;
  }

  get isNotFound(): boolean {
    return this.statusCode === 404;
  }
  get isUnauthorized(): boolean {
    return this.statusCode === 401;
  }
  get isForbidden(): boolean {
    return this.statusCode === 403;
  }
  get isConflict(): boolean {
    return this.statusCode === 409;
  }
  get isUnavailable(): boolean {
    return this.statusCode === 503 || this.statusCode === 0;
  }
  get isTimeout(): boolean {
    return this.statusCode === 408;
  }
}

// ---------------------------------------------------------------------------
// Typed sentinel subclasses (mirror Go's errors.Is sentinels and the
// Kotlin/C# typed exceptions). Each accepts either a plain message or a full
// ApiErrorInit so parseApiError can carry code/requestId/details through.
// ---------------------------------------------------------------------------

function normalizeInit(
  init: string | ApiErrorInit,
  fallbackMessage: string
): ApiErrorInit {
  if (typeof init === "string") return { message: init };
  return init ?? { message: fallbackMessage };
}

export class OrbUnauthorizedError extends OrbApiError {
  constructor(init: string | ApiErrorInit = "orb: unauthorized") {
    super({ ...normalizeInit(init, "orb: unauthorized"), statusCode: 401 });
    this.name = "OrbUnauthorizedError";
  }
}

export class OrbForbiddenError extends OrbApiError {
  constructor(init: string | ApiErrorInit = "orb: forbidden") {
    super({ ...normalizeInit(init, "orb: forbidden"), statusCode: 403 });
    this.name = "OrbForbiddenError";
  }
}

export class OrbNotFoundError extends OrbApiError {
  constructor(init: string | ApiErrorInit = "orb: not found") {
    super({ ...normalizeInit(init, "orb: not found"), statusCode: 404 });
    this.name = "OrbNotFoundError";
  }
}

export class OrbConflictError extends OrbApiError {
  constructor(init: string | ApiErrorInit = "orb: conflict") {
    super({ ...normalizeInit(init, "orb: conflict"), statusCode: 409 });
    this.name = "OrbConflictError";
  }
}

export class OrbTimeoutError extends OrbApiError {
  constructor(init: string | ApiErrorInit = "orb: request timeout") {
    super({ ...normalizeInit(init, "orb: request timeout"), statusCode: 408 });
    this.name = "OrbTimeoutError";
  }
}

export class OrbUnavailableError extends OrbApiError {
  constructor(init: string | ApiErrorInit = "orb: service unavailable") {
    super({ ...normalizeInit(init, "orb: service unavailable"), statusCode: 503 });
    this.name = "OrbUnavailableError";
  }
}

/**
 * Construct the most specific OrbApiError subclass for an HTTP status so that
 * `instanceof OrbNotFoundError` (etc.) works for callers, falling back to the
 * base OrbApiError for statuses without a typed sentinel.
 */
export function apiErrorForStatus(opts: ApiErrorInit & { statusCode: number }): OrbApiError {
  switch (opts.statusCode) {
    case 401:
      return new OrbUnauthorizedError(opts);
    case 403:
      return new OrbForbiddenError(opts);
    case 404:
      return new OrbNotFoundError(opts);
    case 409:
      return new OrbConflictError(opts);
    case 408:
      return new OrbTimeoutError(opts);
    case 503:
      return new OrbUnavailableError(opts);
    default:
      return new OrbApiError(opts);
  }
}

// ---------------------------------------------------------------------------
// Parse an Axios error / response into a typed OrbApiError
// ---------------------------------------------------------------------------

/** Extract the server-assigned request ID from response headers. */
function requestIdFromHeaders(headers: unknown): string | null {
  if (!headers || typeof headers !== "object") return null;
  const lower: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(headers as Record<string, unknown>)) {
    lower[k.toLowerCase()] = v;
  }
  for (const h of ["x-request-id", "x-correlation-id"]) {
    const v = lower[h];
    if (typeof v === "string" && v !== "") return v;
  }
  return null;
}

export function parseApiError(err: unknown): OrbApiError {
  if (err instanceof OrbApiError) return err;

  if (axios.isAxiosError(err)) {
    const status = err.response?.status ?? 0;
    const requestId = requestIdFromHeaders(err.response?.headers);
    const body = err.response?.data as
      | { error?: { code?: string; message?: string }; detail?: unknown }
      | undefined;

    if (body?.error?.message) {
      return apiErrorForStatus({
        statusCode: status,
        code: body.error.code ?? null,
        message: body.error.message,
        requestId,
        details: body.detail,
      });
    }

    // FastAPI validation error shape
    if (body?.detail) {
      const detail = body.detail;
      const msg =
        typeof detail === "string" ? detail : JSON.stringify(detail);
      return apiErrorForStatus({
        statusCode: status,
        message: msg,
        requestId,
        details: detail,
      });
    }

    if (err.code === "ECONNREFUSED" || err.code === "ENOENT") {
      return new OrbApiError({
        statusCode: 0,
        code: err.code,
        message: `orb: connection failed: ${err.message}`,
        requestId,
      });
    }

    return apiErrorForStatus({
      statusCode: status,
      message: err.message,
      requestId,
    });
  }

  if (err instanceof Error) {
    return new OrbApiError({ statusCode: 0, message: err.message });
  }

  return new OrbApiError({ statusCode: 0, message: String(err) });
}
