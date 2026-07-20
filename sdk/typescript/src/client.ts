/**
 * ORB TypeScript SDK — Public Client
 *
 * Covers all 44 operations from sdk/spec/openapi.json.
 * Uses generated models from ../generated/ for typed request/response shapes.
 *
 * Two operating modes:
 *   - spawn: client starts ORB as a child process (UDS transport)
 *   - remote: client connects to an existing ORB instance (TCP/HTTPS)
 */

import axios, { type AxiosInstance, type AxiosRequestConfig } from "axios";
import type { Readable } from "stream";
import { makeUdsAdapter } from "./transport/uds.js";
import { attachRetry, disableRetry, type RetryConfig } from "./transport/retry.js";
import { attachAuth, type AuthOption } from "./auth/index.js";
import { SubprocessManager, tempSocketPath, type ProcessConfig } from "./process/manager.js";
import {
  sseStream,
  parseOrbPayload,
  isSentinel,
  TERMINAL_STATUSES,
  type SseFrame,
  type OrbSseRequest,
} from "./sse/reader.js";
import { parseApiError, OrbApiError, OrbUnavailableError } from "./errors.js";
import type { AxiosResponseHeaders, RawAxiosResponseHeaders } from "axios";

// Re-export generated models
export type {
  TemplateItem,
  TemplateListResponse,
  TemplateCreateRequest,
  TemplateUpdateRequest,
  TemplateMutationResponse,
  MachineItem,
  MachineListResponse,
  MachineReferenceDTO,
  RequestItem,
  RequestMachinesRequest,
  RequestOperationResponse,
  RequestStatusResponse,
  ReturnMachinesRequest,
  BatchRequestStatusBody,
  InitBody,
  CleanupDatabaseBody,
  GenerateTemplatesBody,
  SaveRequest,
  SetValueRequest,
} from "../generated/models/index.js";

export type { AuthOption } from "./auth/index.js";
export type { ProcessConfig } from "./process/manager.js";
export type { OrbSseRequest, OrbMachine, OrbSsePayload } from "./sse/reader.js";
export {
  OrbError,
  OrbApiError,
  OrbUnauthorizedError,
  OrbForbiddenError,
  OrbNotFoundError,
  OrbConflictError,
  OrbTimeoutError,
  OrbUnavailableError,
} from "./errors.js";

// ---------------------------------------------------------------------------
// Stream event type (high-level, consumer-facing)
// ---------------------------------------------------------------------------

export interface StreamEvent {
  requestId: string;
  status: string;
  message?: string;
  requestedCount?: number;
  successfulCount?: number;
  failedCount?: number;
  machines: Array<{
    machineId: string;
    name?: string;
    status?: string;
    result?: string;
    privateIp?: string;
    publicIp?: string;
    launchTime?: string;
    message?: string;
  }>;
}

// ---------------------------------------------------------------------------
// Client configuration
// ---------------------------------------------------------------------------

export interface ClientConfig {
  /** Base URL for remote mode (default: http://localhost:8000) */
  baseUrl?: string;
  /** Authentication strategy (default: none) */
  auth?: AuthOption;
  /** HTTP timeout in ms (default: 30_000) */
  timeoutMs?: number;
  /** Retry configuration */
  retry?: RetryConfig;
  /** If set, start and manage an ORB subprocess */
  process?: ProcessConfig & {
    /** PYTHONPATH to inject when running from source */
    pythonPath?: string;
  };
  /** UNIX socket path for UDS mode without managed subprocess */
  socketPath?: string;
  /** X-ORB-Scheduler header value (for HostFactory scheduler) */
  scheduler?: "default" | "hostfactory";
}

// ---------------------------------------------------------------------------
// OrbClient
// ---------------------------------------------------------------------------

export class OrbClient {
  private readonly http: AxiosInstance;
  private readonly baseUrl: string;
  private readonly scheduler: string;
  private proc: SubprocessManager | null = null;

  private constructor(
    http: AxiosInstance,
    baseUrl: string,
    scheduler: string,
    proc: SubprocessManager | null
  ) {
    this.http = http;
    this.baseUrl = baseUrl;
    this.scheduler = scheduler;
    this.proc = proc;
  }

  /**
   * Create and initialize an OrbClient.
   * If config.process is set, the ORB subprocess is started here.
   */
  static async create(config: ClientConfig = {}): Promise<OrbClient> {
    const auth: AuthOption = config.auth ?? { type: "none" };
    const timeoutMs = config.timeoutMs ?? 30_000;
    const scheduler = config.scheduler ?? "default";

    let socketPath = config.socketPath ?? "";
    let proc: SubprocessManager | null = null;

    if (config.process) {
      if (!socketPath) {
        socketPath = config.process.socketPath ?? tempSocketPath();
      }
      proc = new SubprocessManager({
        ...config.process,
        socketPath,
      });
      await proc.start();
    }

    const baseUrl = socketPath
      ? "http://localhost"
      : (config.baseUrl ?? "http://localhost:8000");

    const instance = axios.create({
      baseURL: baseUrl,
      timeout: timeoutMs,
      adapter: socketPath ? makeUdsAdapter(socketPath) : undefined,
      validateStatus: () => true, // we handle all statuses ourselves
    });

    // Layer 4: auth (applied before retry so auth errors don't get retried)
    attachAuth(instance, auth);

    // Layer 3: retry
    attachRetry(instance, config.retry);

    return new OrbClient(instance, baseUrl, scheduler, proc);
  }

  /**
   * Stop the managed subprocess (if any) and release resources.
   */
  async close(): Promise<void> {
    if (this.proc) {
      await this.proc.stop();
      this.proc = null;
    }
  }

  /**
   * Returns true if the managed process (if any) is currently healthy.
   */
  get healthy(): boolean {
    return this.proc ? this.proc.healthy : true;
  }

  // ---------------------------------------------------------------------------
  // Private HTTP helpers
  // ---------------------------------------------------------------------------

  private checkHealth(): void {
    if (this.proc && !this.proc.healthy) {
      throw new OrbUnavailableError("managed ORB process is unhealthy");
    }
  }

  private schedulerHeaders(): Record<string, string> {
    if (this.scheduler !== "default") {
      return { "X-ORB-Scheduler": this.scheduler };
    }
    return {};
  }

  private async get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
    this.checkHealth();
    const cfg: AxiosRequestConfig = {
      headers: { Accept: "application/json", ...this.schedulerHeaders() },
      params,
    };
    const resp = await this.http.get<T>(path, cfg);
    if (resp.status >= 400) throw parseApiError(this.makeAxiosError(resp));
    return resp.data;
  }

  private async post<T>(path: string, body?: unknown): Promise<T> {
    this.checkHealth();
    const cfg: AxiosRequestConfig = {
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...this.schedulerHeaders(),
      },
    };
    const resp = await this.http.post<T>(path, body ?? null, cfg);
    if (resp.status >= 400) throw parseApiError(this.makeAxiosError(resp));
    return resp.data;
  }

  private async put<T>(path: string, body?: unknown): Promise<T> {
    this.checkHealth();
    const cfg: AxiosRequestConfig = {
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...this.schedulerHeaders(),
      },
    };
    const resp = await this.http.put<T>(path, body ?? null, cfg);
    if (resp.status >= 400) throw parseApiError(this.makeAxiosError(resp));
    return resp.data;
  }

  private async delete<T>(path: string, params?: Record<string, unknown>): Promise<T> {
    this.checkHealth();
    const cfg: AxiosRequestConfig = {
      headers: { Accept: "application/json", ...this.schedulerHeaders() },
      params,
    };
    const resp = await this.http.delete<T>(path, cfg);
    if (resp.status >= 400) throw parseApiError(this.makeAxiosError(resp));
    return resp.data;
  }

  /** Build a synthetic AxiosError from a raw response (used with validateStatus: true) */
  private makeAxiosError(resp: {
    status: number;
    data: unknown;
    headers?: RawAxiosResponseHeaders | AxiosResponseHeaders;
    config?: unknown;
  }) {
    return {
      response: { status: resp.status, data: resp.data, headers: resp.headers ?? {} },
      isAxiosError: true,
      message: `HTTP ${resp.status}`,
      code: undefined,
    };
  }

  // ---------------------------------------------------------------------------
  // System / Observability — 4 operations
  // ---------------------------------------------------------------------------

  /**
   * healthCheck — GET /health
   *
   * A 503 response is a valid, expected result meaning the server is degraded
   * or unhealthy — it carries a parsed health body, not an error. health()
   * therefore returns the parsed body for both 200 and 503 (matching the
   * Go/Kotlin/.NET SDKs) so a health-poll loop sees the degraded status rather
   * than an exception. Other non-2xx statuses (e.g. 401/500) still throw.
   */
  async health(): Promise<{ status: string }> {
    this.checkHealth();
    const resp = await this.http.get<{ status: string }>(
      "/health",
      disableRetry({
        headers: { Accept: "application/json", ...this.schedulerHeaders() },
      })
    );
    // 503 = degraded/unhealthy but valid health body; do not treat as an error.
    if (resp.status >= 400 && resp.status !== 503) {
      throw parseApiError(this.makeAxiosError(resp));
    }
    return resp.data;
  }

  /** getServiceInfo — GET /info */
  async info(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>("/info");
  }

  /** getMetrics — GET /metrics */
  async metrics(): Promise<string> {
    this.checkHealth();
    const resp = await this.http.get<string>("/metrics", {
      headers: { Accept: "text/plain", ...this.schedulerHeaders() },
      responseType: "text",
    });
    if (resp.status >= 400) throw parseApiError(this.makeAxiosError(resp));
    return resp.data;
  }

  /** getDashboardSummary — GET /api/v1/system/dashboard */
  async getDashboardSummary(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>("/api/v1/system/dashboard");
  }

  // ---------------------------------------------------------------------------
  // Templates — 8 operations
  // ---------------------------------------------------------------------------

  /** listTemplates — GET /api/v1/templates/ */
  async listTemplates(): Promise<import("../generated/models/index.js").TemplateListResponse> {
    return this.get<import("../generated/models/index.js").TemplateListResponse>(
      "/api/v1/templates/"
    );
  }

  /** getTemplate — GET /api/v1/templates/{template_id} */
  async getTemplate(
    templateId: string
  ): Promise<import("../generated/models/index.js").TemplateItem> {
    return this.get<import("../generated/models/index.js").TemplateItem>(
      `/api/v1/templates/${encodeURIComponent(templateId)}`
    );
  }

  /** createTemplate — POST /api/v1/templates/ */
  async createTemplate(
    body: import("../generated/models/index.js").TemplateCreateRequest
  ): Promise<import("../generated/models/index.js").TemplateMutationResponse> {
    return this.post<import("../generated/models/index.js").TemplateMutationResponse>(
      "/api/v1/templates/",
      body
    );
  }

  /** updateTemplate — PUT /api/v1/templates/{template_id} */
  async updateTemplate(
    templateId: string,
    body: import("../generated/models/index.js").TemplateUpdateRequest
  ): Promise<import("../generated/models/index.js").TemplateMutationResponse> {
    return this.put<import("../generated/models/index.js").TemplateMutationResponse>(
      `/api/v1/templates/${encodeURIComponent(templateId)}`,
      body
    );
  }

  /** deleteTemplate — DELETE /api/v1/templates/{template_id} */
  async deleteTemplate(templateId: string): Promise<unknown> {
    return this.delete<unknown>(
      `/api/v1/templates/${encodeURIComponent(templateId)}`
    );
  }

  /** validateTemplate — POST /api/v1/templates/validate */
  async validateTemplate(body: unknown): Promise<unknown> {
    return this.post<unknown>("/api/v1/templates/validate", body);
  }

  /** refreshTemplates — POST /api/v1/templates/refresh */
  async refreshTemplates(): Promise<import("../generated/models/index.js").TemplateListResponse> {
    return this.post<import("../generated/models/index.js").TemplateListResponse>(
      "/api/v1/templates/refresh"
    );
  }

  /** generateTemplates — POST /api/v1/templates/generate */
  async generateTemplates(
    body: import("../generated/models/index.js").GenerateTemplatesBody
  ): Promise<import("../generated/models/index.js").TemplateListResponse> {
    return this.post<import("../generated/models/index.js").TemplateListResponse>(
      "/api/v1/templates/generate",
      body
    );
  }

  // ---------------------------------------------------------------------------
  // Machines — 8 operations
  // ---------------------------------------------------------------------------

  /** listMachines — GET /api/v1/machines/ */
  async listMachines(params?: {
    status?: string;
    request_id?: string;
    limit?: number;
    offset?: number;
  }): Promise<import("../generated/models/index.js").MachineListResponse> {
    return this.get<import("../generated/models/index.js").MachineListResponse>(
      "/api/v1/machines/",
      params as Record<string, unknown>
    );
  }

  /** getMachine — GET /api/v1/machines/{machine_id} */
  async getMachine(
    machineId: string
  ): Promise<import("../generated/models/index.js").MachineItem> {
    return this.get<import("../generated/models/index.js").MachineItem>(
      `/api/v1/machines/${encodeURIComponent(machineId)}`
    );
  }

  /** requestMachines — POST /api/v1/machines/request */
  async requestMachines(
    body: import("../generated/models/index.js").RequestMachinesRequest
  ): Promise<import("../generated/models/index.js").RequestOperationResponse> {
    return this.post<import("../generated/models/index.js").RequestOperationResponse>(
      "/api/v1/machines/request",
      body
    );
  }

  /** returnMachines — POST /api/v1/machines/return */
  async returnMachines(
    body: import("../generated/models/index.js").ReturnMachinesRequest
  ): Promise<import("../generated/models/index.js").RequestOperationResponse> {
    return this.post<import("../generated/models/index.js").RequestOperationResponse>(
      "/api/v1/machines/return",
      body
    );
  }

  /** syncMachineStatus — GET /api/v1/machines/{machine_id}/status */
  async syncMachineStatus(
    machineId: string
  ): Promise<import("../generated/models/index.js").MachineListResponse> {
    return this.get<import("../generated/models/index.js").MachineListResponse>(
      `/api/v1/machines/${encodeURIComponent(machineId)}/status`
    );
  }

  /** getMachineMetrics — GET /api/v1/machines/{machine_id}/metrics */
  async getMachineMetrics(
    machineId: string,
    params?: { range?: string }
  ): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>(
      `/api/v1/machines/${encodeURIComponent(machineId)}/metrics`,
      params as Record<string, unknown>
    );
  }

  /** purgeMachine — DELETE /api/v1/machines/{machine_id} */
  async purgeMachine(machineId: string): Promise<unknown> {
    return this.delete<unknown>(
      `/api/v1/machines/${encodeURIComponent(machineId)}`
    );
  }

  // ---------------------------------------------------------------------------
  // Requests — 11 operations
  // ---------------------------------------------------------------------------

  /** listRequests — GET /api/v1/requests/ */
  async listRequests(params?: {
    status?: string;
    limit?: number;
    offset?: number;
    sync?: boolean;
    cursor?: string;
    q?: string;
    sort?: string;
    provider_name?: string;
    provider_type?: string;
    template_id?: string;
    request_type?: string;
    filter_expressions?: string[];
  }): Promise<{ requests: import("../generated/models/index.js").RequestItem[] }> {
    return this.get<{ requests: import("../generated/models/index.js").RequestItem[] }>(
      "/api/v1/requests/",
      params as Record<string, unknown>
    );
  }

  /** listReturnRequests — GET /api/v1/requests/return */
  async listReturnRequests(params?: {
    limit?: number;
    offset?: number;
    cursor?: string;
    q?: string;
    sort?: string;
    provider_name?: string;
    provider_type?: string;
    filter_expressions?: string[];
  }): Promise<{ requests: import("../generated/models/index.js").RequestItem[] }> {
    return this.get<{ requests: import("../generated/models/index.js").RequestItem[] }>(
      "/api/v1/requests/return",
      params as Record<string, unknown>
    );
  }

  /** getRequestStatus — GET /api/v1/requests/{request_id}/status */
  async getRequestStatus(
    requestId: string,
    verbose?: boolean
  ): Promise<import("../generated/models/index.js").RequestStatusResponse> {
    return this.get<import("../generated/models/index.js").RequestStatusResponse>(
      `/api/v1/requests/${encodeURIComponent(requestId)}/status`,
      verbose ? { verbose: "true" } : undefined
    );
  }

  /** getRequest — GET /api/v1/requests/{request_id} */
  async getRequest(
    requestId: string,
    verbose?: boolean
  ): Promise<import("../generated/models/index.js").RequestStatusResponse> {
    return this.get<import("../generated/models/index.js").RequestStatusResponse>(
      `/api/v1/requests/${encodeURIComponent(requestId)}`,
      verbose ? { verbose: "true" } : undefined
    );
  }

  /** getRequestTimeline — GET /api/v1/requests/{request_id}/timeline */
  async getRequestTimeline(requestId: string): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>(
      `/api/v1/requests/${encodeURIComponent(requestId)}/timeline`
    );
  }

  /** batchGetRequestStatus — POST /api/v1/requests/status */
  async batchGetRequestStatus(
    body: import("../generated/models/index.js").BatchRequestStatusBody
  ): Promise<{ requests: import("../generated/models/index.js").RequestItem[] }> {
    return this.post<{ requests: import("../generated/models/index.js").RequestItem[] }>(
      "/api/v1/requests/status",
      body
    );
  }

  /** cancelRequest — DELETE /api/v1/requests/{request_id} */
  async cancelRequest(requestId: string, reason?: string): Promise<unknown> {
    return this.delete<unknown>(
      `/api/v1/requests/${encodeURIComponent(requestId)}`,
      reason !== undefined ? { reason } : undefined
    );
  }

  /** purgeRequest — POST /api/v1/requests/{request_id}/purge */
  async purgeRequest(requestId: string): Promise<unknown> {
    return this.post<unknown>(
      `/api/v1/requests/${encodeURIComponent(requestId)}/purge`
    );
  }

  /**
   * streamRequest
   * GET /api/v1/requests/{request_id}/stream
   *
   * Returns an AsyncGenerator that yields StreamEvent objects.
   * Reconnects with back-off if the connection is dropped.
   * Auth headers are applied on each (re)connection.
   */
  async *streamRequestStatus(
    requestId: string,
    opts: {
      intervalSeconds?: number;
      timeoutSeconds?: number;
      signal?: AbortSignal;
    } = {}
  ): AsyncGenerator<StreamEvent> {
    this.checkHealth();
    const interval = opts.intervalSeconds ?? 2;
    const timeout = opts.timeoutSeconds ?? 300;
    const { signal } = opts;

    const self = this;

    async function connect(lastEventId?: string): Promise<Readable> {
      if (signal?.aborted) throw new Error("aborted");

      const path =
        `/api/v1/requests/${encodeURIComponent(requestId)}/stream` +
        `?interval=${interval}&timeout=${timeout}`;

      const headers: Record<string, string> = {
        Accept: "text/event-stream",
        ...self.schedulerHeaders(),
      };
      if (lastEventId) {
        headers["Last-Event-ID"] = lastEventId;
      }

      // Build the full URL for auth interceptor to sign correctly
      const fullUrl = `${self.baseUrl}${path}`;

      const resp = await self.http.get(fullUrl, {
        headers,
        responseType: "stream",
        validateStatus: () => true,
        signal,
      });

      if (resp.status >= 400) {
        // The response stream is already open even on an error status; destroy
        // it before throwing so the underlying socket/PassThrough is released
        // rather than leaked (the success path hands the stream to the caller,
        // which destroys it on completion/abort).
        (resp.data as Readable | undefined)?.destroy?.();
        const err = new OrbApiError({
          statusCode: resp.status,
          message: `SSE stream returned HTTP ${resp.status}`,
        });
        throw err;
      }

      const readable = resp.data as Readable;
      // When signal is aborted, destroy the stream so parseFrames terminates
      if (signal && typeof readable?.destroy === "function") {
        signal.addEventListener("abort", () => readable.destroy(), { once: true });
      }
      return readable;
    }

    for await (const frame of sseStream(connect, { signal })) {
      if (isSentinel(frame)) return;

      const payload = parseOrbPayload(frame);
      if (!payload) continue;

      const requests = payload.requests ?? [];
      for (const req of requests) {
        const event = orbSseRequestToStreamEvent(req);
        yield event;
        if (TERMINAL_STATUSES.has(req.status)) return;
      }
    }
  }

  /**
   * Wait for a request to reach a terminal status.
   * Returns the final StreamEvent.
   */
  async waitForCompletion(
    requestId: string,
    opts: {
      intervalSeconds?: number;
      timeoutSeconds?: number;
      signal?: AbortSignal;
    } = {}
  ): Promise<StreamEvent> {
    let last: StreamEvent | null = null;
    for await (const event of this.streamRequestStatus(requestId, opts)) {
      last = event;
    }
    if (!last) {
      throw new OrbApiError({ statusCode: 0, message: "stream ended without any events" });
    }
    return last;
  }

  /**
   * streamEvents — GET /api/v1/events/
   *
   * Global SSE event bus. Returns an AsyncGenerator of raw SSE frames.
   * Auth headers are applied on each (re)connection.
   */
  async *streamEvents(
    opts: { signal?: AbortSignal } = {}
  ): AsyncGenerator<SseFrame> {
    this.checkHealth();
    const self = this;
    const { signal } = opts;

    async function connect(lastEventId?: string): Promise<Readable> {
      if (signal?.aborted) throw new Error("aborted");

      const headers: Record<string, string> = {
        Accept: "text/event-stream",
        ...self.schedulerHeaders(),
      };
      if (lastEventId) headers["Last-Event-ID"] = lastEventId;

      const fullUrl = `${self.baseUrl}/api/v1/events/`;
      const resp = await self.http.get(fullUrl, {
        headers,
        responseType: "stream",
        validateStatus: () => true,
        signal,
      });
      if (resp.status >= 400) {
        // Destroy the already-opened response stream before throwing so the
        // underlying socket/PassThrough is released rather than leaked.
        (resp.data as Readable | undefined)?.destroy?.();
        throw new OrbApiError({
          statusCode: resp.status,
          message: `Event stream returned HTTP ${resp.status}`,
        });
      }

      const readable = resp.data as Readable;
      // When signal is aborted, destroy the stream so parseFrames terminates
      if (signal && typeof readable?.destroy === "function") {
        signal.addEventListener("abort", () => readable.destroy(), { once: true });
      }
      return readable;
    }

    yield* sseStream(connect, { signal });
  }

  // ---------------------------------------------------------------------------
  // Providers — 4 operations
  // ---------------------------------------------------------------------------

  /** listProviders — GET /api/v1/providers/ */
  async listProviders(): Promise<{ providers: unknown[] }> {
    return this.get<{ providers: unknown[] }>("/api/v1/providers/");
  }

  /** getAllProviderSchemas — GET /api/v1/providers/schemas */
  async getAllProviderSchemas(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>("/api/v1/providers/schemas");
  }

  /** getProviderSchema — GET /api/v1/providers/{name}/schema */
  async getProviderSchema(name: string): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>(
      `/api/v1/providers/${encodeURIComponent(name)}/schema`
    );
  }

  /** getProvidersHealth — GET /api/v1/providers/health */
  async getProvidersHealth(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>("/api/v1/providers/health");
  }

  // ---------------------------------------------------------------------------
  // Config — 7 operations
  // ---------------------------------------------------------------------------

  /** getFullConfig — GET /api/v1/config/ */
  async getFullConfig(params?: { source?: string }): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>(
      "/api/v1/config/",
      params as Record<string, unknown>
    );
  }

  /** getConfigSources — GET /api/v1/config/sources */
  async getConfigSources(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>("/api/v1/config/sources");
  }

  /** getConfigValue — GET /api/v1/config/{key} */
  async getConfigValue(key: string): Promise<unknown> {
    return this.get<unknown>(`/api/v1/config/${encodeURIComponent(key)}`);
  }

  /** setConfigValue — PUT /api/v1/config/{key} */
  async setConfigValue(
    key: string,
    body: import("../generated/models/index.js").SetValueRequest
  ): Promise<unknown> {
    return this.put<unknown>(`/api/v1/config/${encodeURIComponent(key)}`, body);
  }

  /** saveConfig — POST /api/v1/config/save */
  async saveConfig(
    body: import("../generated/models/index.js").SaveRequest
  ): Promise<unknown> {
    return this.post<unknown>("/api/v1/config/save", body);
  }

  /** validateConfig — POST /api/v1/config/validate */
  async validateConfig(): Promise<unknown> {
    return this.post<unknown>("/api/v1/config/validate");
  }

  // ---------------------------------------------------------------------------
  // Admin — 4 operations
  // ---------------------------------------------------------------------------

  /** wipeDatabase — POST /api/v1/admin/database/wipe */
  async wipeDatabase(body: { confirm: boolean }): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>("/api/v1/admin/database/wipe", body);
  }

  /** initOrb — POST /api/v1/admin/init */
  async initOrb(
    body: import("../generated/models/index.js").InitBody
  ): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>("/api/v1/admin/init", body);
  }

  /** cleanupDatabase — POST /api/v1/admin/database/cleanup */
  async cleanupDatabase(
    body: import("../generated/models/index.js").CleanupDatabaseBody
  ): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>("/api/v1/admin/database/cleanup", body);
  }

  /** reloadConfig — POST /api/v1/admin/reload-config */
  async reloadConfig(): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>("/api/v1/admin/reload-config");
  }

  // ---------------------------------------------------------------------------
  // Me / Observability — 2 operations
  // ---------------------------------------------------------------------------

  /** getCurrentUser — GET /api/v1/me/ */
  async getMe(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>("/api/v1/me/");
  }

  /** getTelemetryStatus — GET /api/v1/observability/telemetry */
  async getTelemetryStatus(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>("/api/v1/observability/telemetry");
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function orbSseRequestToStreamEvent(req: OrbSseRequest): StreamEvent {
  return {
    requestId: req.request_id,
    status: req.status,
    message: req.message,
    requestedCount: req.requested_count,
    successfulCount: req.successful_count,
    failedCount: req.failed_count,
    machines: (req.machines ?? []).map((m) => ({
      machineId: m.machine_id,
      name: m.name,
      status: m.status,
      result: m.result,
      privateIp: m.private_ip,
      publicIp: m.public_ip,
      launchTime: m.launch_time,
      message: m.message,
    })),
  };
}
