/**
 * Layer 3: Retry with Exponential Back-off
 *
 * Retries transient failures ONLY for idempotent methods (GET/HEAD/PUT/DELETE/
 * OPTIONS), because a non-idempotent POST that reached the server before the
 * socket dropped could otherwise be silently executed twice (e.g. double
 * machine provisioning).
 *
 * Idempotent methods are retried on:
 *   - HTTP 429 and 503 (server rejected/unavailable)
 *   - HTTP 5xx
 *   - Network errors (connection refused, reset, socket hang-up, timeout)
 *
 * POST is retried ONLY on a pre-write connection failure (ECONNREFUSED —
 * the connection was refused so the request never reached the server). POST is
 * NEVER auto-retried on 429/503 or on a post-write network error, since the
 * request may already have been processed.
 *
 * Never retried at all:
 *   - 4xx (except 429 on idempotent methods)
 *   - Requests cancelled via AbortSignal / CancelToken
 *   - Requests marked with disableRetry()
 */

import axios, {
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from "axios";

export interface RetryConfig {
  maxRetries?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
}

const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "PUT", "DELETE", "OPTIONS"]);

/**
 * Pre-write network failures for which even a POST is safe to retry: the
 * connection was never established, so the server could not have processed the
 * request. Any other network error on a POST is treated as post-write (the
 * request may have reached the server) and is NOT retried.
 */
const PRE_WRITE_NETWORK_CODES = new Set(["ECONNREFUSED", "ENOTFOUND", "EAI_AGAIN"]);

function isIdempotent(method: string): boolean {
  return IDEMPOTENT_METHODS.has(method.toUpperCase());
}

function shouldRetryStatus(method: string, status: number): boolean {
  // Non-idempotent (POST/PATCH): never retry on any HTTP status — a 429/503
  // may have been produced after the server already processed the write.
  if (!isIdempotent(method)) return false;
  if (status === 429 || status === 503) return true;
  if (status >= 500) return true;
  return false;
}

function shouldRetryNetworkError(method: string, code: string | undefined): boolean {
  if (isIdempotent(method)) return true;
  // POST/PATCH: only retry if the connection was refused/never established.
  return code !== undefined && PRE_WRITE_NETWORK_CODES.has(code);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jitter(ms: number): number {
  // Add 0-50% jitter
  return Math.floor(ms * (1 + Math.random() * 0.5));
}

const RETRY_COUNT_KEY = "__orb_retry_count__";

/**
 * Config key that, when set truthy on an Axios request config, disables the
 * retry interceptor for that single request. Used by health() so a 503
 * degraded response is observed directly rather than retried away.
 */
export const DISABLE_RETRY_KEY = "__orb_disable_retry__";

/** Mark an Axios request config so the retry interceptor skips it. */
export function disableRetry<T extends object>(config: T): T {
  (config as Record<string, unknown>)[DISABLE_RETRY_KEY] = true;
  return config;
}

/**
 * Attach retry interceptors to an existing Axios instance.
 */
export function attachRetry(instance: AxiosInstance, cfg: RetryConfig = {}): void {
  const maxRetries = cfg.maxRetries ?? 3;
  const baseDelayMs = cfg.baseDelayMs ?? 500;
  const maxDelayMs = cfg.maxDelayMs ?? 30_000;

  instance.interceptors.response.use(
    undefined,
    async (error: unknown) => {
      if (!axios.isAxiosError(error)) throw error;
      if (axios.isCancel(error)) throw error;

      const config = error.config as (InternalAxiosRequestConfig & Record<string, unknown>) | undefined;
      if (!config) throw error;

      // Explicitly opted out (e.g. health() must observe a 503 directly).
      if (config[DISABLE_RETRY_KEY]) throw error;

      // Get the HTTP method from the real request config
      const method = (config.method ?? "GET").toUpperCase();
      const status = error.response?.status;

      // Network error (no HTTP status).
      const isNetworkError = status === undefined;

      if (isNetworkError) {
        if (!shouldRetryNetworkError(method, error.code)) throw error;
      } else if (!shouldRetryStatus(method, status!)) {
        throw error;
      }

      const attempt = (config[RETRY_COUNT_KEY] as number | undefined) ?? 0;
      if (attempt >= maxRetries) throw error;

      const rawDelay = baseDelayMs * Math.pow(2, attempt);
      const delay = Math.min(jitter(rawDelay), maxDelayMs);
      await sleep(delay);

      config[RETRY_COUNT_KEY] = attempt + 1;
      return instance.request(config);
    }
  );
}
