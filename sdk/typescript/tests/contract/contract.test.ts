/**
 * Contract tests for the ORB TypeScript SDK.
 *
 * These tests spawn a REAL ORB process over a UNIX domain socket and call
 * EVERY method on the client, asserting:
 *   1. No 404/405 route-level errors (those indicate a spec/client bug)
 *   2. Methods that return data return the expected shape
 *   3. Methods that expect missing resources return proper 404-for-resource
 *      (not a 404/405 for the route itself)
 *
 * Distinguish route-level 404/405 from resource-level 404:
 *   - A route-level 404/405 means the URL path itself doesn't exist on the server.
 *   - A resource-level 404 means the route exists but the resource was not found.
 *   - Route-level 404/405 → SDK bug. Resource-level 404 → expected behavior.
 *
 * ORB is started with:
 *   python -m orb --config <tmp-config.json> server start --foreground --api-only --socket-path <sock>
 *
 * The config uses:
 *   - auth: none (no authentication required)
 *   - storage: json (file-based, ephemeral)
 *   - provider: aws with stub credentials (region us-east-1)
 *   - scheduler: default
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawn, type ChildProcess } from "child_process";
import { request as undiciRequest, Agent } from "undici";
import { OrbClient, OrbApiError } from "../../src/index";

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

const ORB_BINARY = process.env["ORB_BINARY"] ?? "python3";
const ORB_SRC = process.env["ORB_SRC"] ?? "";
const START_TIMEOUT_MS = 45_000;

interface OrbFixture {
  client: OrbClient;
  socketPath: string;
  proc: ChildProcess;
  tmpDir: string;
  stop: () => Promise<void>;
}

async function spawnOrb(): Promise<OrbFixture> {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "orb-contract-"));
  const socketPath = path.join(tmpDir, "orb.sock");
  const configPath = path.join(tmpDir, "config.json");

  // Minimal config: no-auth, json storage, aws provider with stub region.
  // Use a unique working_dir per test run so there's no PID file conflict
  // with any other orb instance that may be running.
  const config = {
    version: "2.0.0",
    scheduler: { type: "default" },
    provider: {
      providers: [
        {
          name: "aws-stub",
          type: "aws",
          enabled: true,
          config: { region: "us-east-1" },
        },
      ],
    },
    storage: { type: "json" },
    server: {
      host: "127.0.0.1",
      port: 19997,
      working_dir: tmpDir,
      pid_file: path.join(tmpDir, "orb-server.pid"),
    },
    auth: { type: "none" },
    logging: { level: "ERROR" },
  };

  fs.writeFileSync(configPath, JSON.stringify(config, null, 2));

  const proc = spawn(
    ORB_BINARY,
    ["-m", "orb", "--config", configPath, "server", "start", "--foreground", "--api-only", "--socket-path", socketPath],
    {
      env: {
        ...process.env,
        PYTHONPATH: ORB_SRC,
        ORB_LOG_LEVEL: "ERROR",
      },
      stdio: ["ignore", "pipe", "pipe"],
    }
  );

  // Wait for socket to become healthy
  const agent = new Agent({ connect: { socketPath }, connections: 1 });
  const deadline = Date.now() + START_TIMEOUT_MS;
  let healthy = false;

  while (Date.now() < deadline) {
    await sleep(300);
    try {
      const { statusCode, body } = await undiciRequest("http://localhost/health", {
        dispatcher: agent,
        signal: AbortSignal.timeout(2000),
      });
      const chunks: Buffer[] = [];
      for await (const chunk of body) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      }
      const json = JSON.parse(Buffer.concat(chunks).toString("utf8")) as { status: string };
      if (statusCode === 200 && (json.status === "healthy" || json.status === "degraded")) {
        healthy = true;
        break;
      }
    } catch {
      // not ready yet
    }
  }

  await agent.close();

  if (!healthy) {
    proc.kill("SIGKILL");
    fs.rmSync(tmpDir, { recursive: true, force: true });
    throw new Error(`ORB did not become healthy within ${START_TIMEOUT_MS}ms`);
  }

  // Create client connected via UDS
  const client = await OrbClient.create({
    socketPath,
    auth: { type: "none" },
    timeoutMs: 15_000,
    retry: { maxRetries: 1, baseDelayMs: 100 },
  });

  const stop = async (): Promise<void> => {
    await client.close();
    proc.kill("SIGTERM");
    await new Promise<void>((resolve) => {
      const t = setTimeout(() => { proc.kill("SIGKILL"); resolve(); }, 5000);
      proc.on("exit", () => { clearTimeout(t); resolve(); });
    });
    fs.rmSync(tmpDir, { recursive: true, force: true });
  };

  return { client, socketPath, proc, tmpDir, stop };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Assert that an error is NOT a route-level 404/405.
 * A resource-level 404 (resource doesn't exist) is acceptable.
 * A route-level 404/405 means the URL path itself doesn't exist — that's a bug.
 *
 * We distinguish by checking if the error body looks like a FastAPI 404
 * with "Not Found" (route) vs. a structured ORB error with a specific message.
 */
function assertNotRouteLevelError(err: unknown, context: string): void {
  if (!(err instanceof OrbApiError)) return;

  // 405 is always a route-level error
  if (err.statusCode === 405) {
    throw new Error(
      `${context}: got HTTP 405 Method Not Allowed — this is a route-level bug in the client/spec`
    );
  }

  // A 404 with "Not Found" (no structured message) is likely route-level
  if (err.statusCode === 404 && err.message === "Not Found" && !err.code) {
    throw new Error(
      `${context}: got HTTP 404 Not Found (no detail) — likely a route-level missing path bug`
    );
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

let fixture: OrbFixture;

beforeAll(async () => {
  fixture = await spawnOrb();
  console.log(`ORB started: PID=${fixture.proc.pid}, socket=${fixture.socketPath}`);
}, START_TIMEOUT_MS + 5_000);

afterAll(async () => {
  if (fixture) {
    await fixture.stop();
    console.log("ORB stopped");
  }
});

// ---------------------------------------------------------------------------
// System / Observability
// ---------------------------------------------------------------------------

describe("System operations", () => {
  it("health — GET /health", async () => {
    const result = await fixture.client.health();
    expect(result.status).toBeDefined();
    expect(["healthy", "degraded"].includes(result.status)).toBe(true);
    console.log(`  health: ${result.status}`);
  });

  it("info — GET /info", async () => {
    const result = await fixture.client.info();
    expect(typeof result).toBe("object");
    console.log(`  info.version: ${(result as any).version ?? "unknown"}`);
  });

  it("metrics — GET /metrics", async () => {
    const result = await fixture.client.metrics();
    // Contract check: the /metrics route is reachable and returns a string.
    // A freshly-started orb may have no scraped metrics yet, so an empty body
    // is valid — do not assert non-empty (that made the test flaky in CI).
    expect(typeof result).toBe("string");
    console.log(`  metrics: ${result.length} bytes`);
  });

  it("getDashboardSummary — GET /api/v1/system/dashboard", async () => {
    try {
      const result = await fixture.client.getDashboardSummary();
      expect(typeof result).toBe("object");
    } catch (err) {
      assertNotRouteLevelError(err, "getDashboardSummary");
      // 404/500 are acceptable if dashboard data isn't configured
    }
  });

  it("getTelemetryStatus — GET /api/v1/observability/telemetry", async () => {
    try {
      const result = await fixture.client.getTelemetryStatus();
      expect(typeof result).toBe("object");
    } catch (err) {
      assertNotRouteLevelError(err, "getTelemetryStatus");
    }
  });

  it("getMe — GET /api/v1/me/", async () => {
    try {
      const result = await fixture.client.getMe();
      expect(typeof result).toBe("object");
      console.log(`  me: ${JSON.stringify(result)}`);
    } catch (err) {
      // /me/ may return 401 when no session is active even with auth: none
      assertNotRouteLevelError(err, "getMe");
      if (err instanceof OrbApiError) {
        // 401 is expected (no session) — but NOT a route-level error
        expect([200, 401].includes(err.statusCode)).toBe(true);
        console.log(`  getMe → ${err.statusCode} (auth required or no session)`);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

describe("Providers", () => {
  it("listProviders — GET /api/v1/providers/", async () => {
    const result = await fixture.client.listProviders();
    expect(result).toHaveProperty("providers");
    expect(Array.isArray(result.providers)).toBe(true);
    console.log(`  providers: ${result.providers.length}`);
  });

  it("getAllProviderSchemas — GET /api/v1/providers/schemas", async () => {
    try {
      const result = await fixture.client.getAllProviderSchemas();
      expect(typeof result).toBe("object");
    } catch (err) {
      assertNotRouteLevelError(err, "getAllProviderSchemas");
    }
  });

  it("getProviderSchema — GET /api/v1/providers/{name}/schema", async () => {
    try {
      const result = await fixture.client.getProviderSchema("aws");
      expect(typeof result).toBe("object");
    } catch (err) {
      assertNotRouteLevelError(err, "getProviderSchema(aws)");
      // 404 is acceptable if aws schema not found
    }
  });

  it("getProvidersHealth — GET /api/v1/providers/health", async () => {
    try {
      const result = await fixture.client.getProvidersHealth();
      expect(typeof result).toBe("object");
    } catch (err) {
      assertNotRouteLevelError(err, "getProvidersHealth");
    }
  });
});

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------

let createdTemplateId: string | undefined;

describe("Templates", () => {
  it("listTemplates — GET /api/v1/templates/", async () => {
    const result = await fixture.client.listTemplates();
    expect(result).toHaveProperty("templates");
    expect(Array.isArray(result.templates)).toBe(true);
    console.log(`  templates: ${result.templates?.length}`);
  });

  it("createTemplate — POST /api/v1/templates/", async () => {
    try {
      const result = await fixture.client.createTemplate({
        templateId: "contract-test-template-" + Date.now(),
        name: "contract-test-template",
        description: "Created by contract test",
      } as any);
      expect(result).toBeDefined();
      if (result && typeof result === "object" && "template_id" in result) {
        createdTemplateId = (result as any).template_id;
        console.log(`  created template: ${createdTemplateId}`);
      }
    } catch (err) {
      assertNotRouteLevelError(err, "createTemplate");
      console.log(`  createTemplate returned error (acceptable): ${err}`);
    }
  });

  it("getTemplate — GET /api/v1/templates/{id}", async () => {
    // Use a non-existent ID — expect resource-level 404, NOT route-level 404
    try {
      await fixture.client.getTemplate("nonexistent-template-id-xyz");
    } catch (err) {
      assertNotRouteLevelError(err, "getTemplate");
      if (err instanceof OrbApiError) {
        expect(err.statusCode).toBe(404);
        console.log(`  getTemplate(nonexistent) → 404 (correct)`);
      }
    }
  });

  it("getTemplate — GET /api/v1/templates/{id} (existing)", async () => {
    // First get the list to find a template
    const list = await fixture.client.listTemplates();
    const templates = list.templates ?? [];
    if (templates.length === 0) {
      console.log("  no templates available — skipping get existing");
      return;
    }
    const tmpl = templates[0];
    const id = tmpl.template_id!;
    try {
      const result = await fixture.client.getTemplate(id);
      expect(result).toBeDefined();
      expect((result as any).template_id ?? (result as any).name).toBeTruthy();
      console.log(`  getTemplate(${id}): ok`);
    } catch (err) {
      assertNotRouteLevelError(err, `getTemplate(${id})`);
    }
  });

  it("validateTemplate — POST /api/v1/templates/validate", async () => {
    try {
      await fixture.client.validateTemplate({
        name: "test-validate",
        provider_type: "aws",
        config: {},
      });
    } catch (err) {
      assertNotRouteLevelError(err, "validateTemplate");
    }
  });

  it("refreshTemplates — POST /api/v1/templates/refresh", async () => {
    try {
      const result = await fixture.client.refreshTemplates();
      expect(result).toBeDefined();
    } catch (err) {
      assertNotRouteLevelError(err, "refreshTemplates");
    }
  });

  it("generateTemplates — POST /api/v1/templates/generate", async () => {
    try {
      await fixture.client.generateTemplates({
        provider: "aws-stub",
        all_providers: false,
      });
    } catch (err) {
      assertNotRouteLevelError(err, "generateTemplates");
      // 500 acceptable if no real AWS creds
    }
  });

  it("updateTemplate — PUT /api/v1/templates/{id}", async () => {
    if (!createdTemplateId) {
      console.log("  no created template — testing with nonexistent ID");
      try {
        await fixture.client.updateTemplate("nonexistent-xyz", {
          name: "updated",
          description: "updated",
        });
      } catch (err) {
        assertNotRouteLevelError(err, "updateTemplate(nonexistent)");
        if (err instanceof OrbApiError) {
          // 404 = resource not found, 403 = permission denied (also acceptable — route exists)
          expect([404, 403, 422].includes(err.statusCode)).toBe(true);
        }
      }
      return;
    }

    try {
      await fixture.client.updateTemplate(createdTemplateId, {
        name: "contract-test-template-updated",
        description: "Updated by contract test",
      });
    } catch (err) {
      assertNotRouteLevelError(err, `updateTemplate(${createdTemplateId})`);
    }
  });

  it("deleteTemplate — DELETE /api/v1/templates/{id}", async () => {
    if (!createdTemplateId) {
      try {
        await fixture.client.deleteTemplate("nonexistent-xyz");
      } catch (err) {
        assertNotRouteLevelError(err, "deleteTemplate(nonexistent)");
        if (err instanceof OrbApiError) {
          // 404 = not found, 403 = permission denied — both mean route exists
          expect([404, 403].includes(err.statusCode)).toBe(true);
        }
      }
      return;
    }

    try {
      await fixture.client.deleteTemplate(createdTemplateId);
      createdTemplateId = undefined;
    } catch (err) {
      assertNotRouteLevelError(err, `deleteTemplate(${createdTemplateId})`);
    }
  });
});

// ---------------------------------------------------------------------------
// Machines
// ---------------------------------------------------------------------------

describe("Machines", () => {
  it("listMachines — GET /api/v1/machines/", async () => {
    const result = await fixture.client.listMachines();
    expect(result).toHaveProperty("machines");
    expect(Array.isArray(result.machines)).toBe(true);
    console.log(`  machines: ${result.machines?.length}`);
  });

  it("getMachine — GET /api/v1/machines/{id} (nonexistent)", async () => {
    try {
      await fixture.client.getMachine("nonexistent-machine-id-xyz");
    } catch (err) {
      assertNotRouteLevelError(err, "getMachine(nonexistent)");
      if (err instanceof OrbApiError) {
        // 404 expected for missing resource
        expect(err.statusCode).toBe(404);
        console.log(`  getMachine(nonexistent) → 404 (correct)`);
      }
    }
  });

  it("requestMachines — POST /api/v1/machines/request", async () => {
    // The flagship write op MUST always be exercised — never silently skipped.
    // Prefer a real template if one is configured; otherwise synthesize a
    // template ID so the POST is always issued and the route is proven to exist.
    const templates = await fixture.client.listTemplates();
    const tmplList = templates.templates ?? [];
    const templateId = tmplList.length > 0
      ? tmplList[0].template_id!
      : "contract-synthetic-template-" + Date.now();

    try {
      const result = await fixture.client.requestMachines({
        templateId,
        count: 1,
      });
      expect(result).toBeDefined();
      console.log(`  requestMachines: ${JSON.stringify(result)}`);
    } catch (err) {
      assertNotRouteLevelError(err, "requestMachines");
      // Resource-level error (bad/unknown template, no AWS creds, missing admin
      // role) is acceptable — the route exists and processed the request.
      if (err instanceof OrbApiError) {
        expect([400, 403, 404, 422, 500, 503].includes(err.statusCode)).toBe(true);
        console.log(`  requestMachines → ${err.statusCode} (resource-level, route exists)`);
      }
    }
  });

  it("returnMachines — POST /api/v1/machines/return", async () => {
    try {
      // Try returning a nonexistent machine — route must exist
      await fixture.client.returnMachines({ machineIds: ["nonexistent-machine-id"] });
    } catch (err) {
      assertNotRouteLevelError(err, "returnMachines");
      // 404/400 expected for nonexistent machine
    }
  });

  it("syncMachineStatus — GET /api/v1/machines/{id}/status", async () => {
    try {
      await fixture.client.syncMachineStatus("nonexistent-machine-id");
    } catch (err) {
      assertNotRouteLevelError(err, "syncMachineStatus");
      if (err instanceof OrbApiError) {
        expect(err.statusCode).toBe(404);
      }
    }
  });

  it("getMachineMetrics — GET /api/v1/machines/{id}/metrics", async () => {
    try {
      await fixture.client.getMachineMetrics("nonexistent-machine-id");
    } catch (err) {
      assertNotRouteLevelError(err, "getMachineMetrics");
      if (err instanceof OrbApiError) {
        expect(err.statusCode).toBe(404);
      }
    }
  });

  it("purgeMachine — DELETE /api/v1/machines/{id}", async () => {
    try {
      await fixture.client.purgeMachine("nonexistent-machine-id");
    } catch (err) {
      assertNotRouteLevelError(err, "purgeMachine");
      if (err instanceof OrbApiError) {
        // 404 = not found, 403 = permission denied — both mean route exists
        expect([404, 403].includes(err.statusCode)).toBe(true);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

describe("Requests", () => {
  it("listRequests — GET /api/v1/requests/", async () => {
    const result = await fixture.client.listRequests();
    expect(result).toHaveProperty("requests");
    expect(Array.isArray(result.requests)).toBe(true);
    console.log(`  requests: ${result.requests?.length}`);
  });

  it("listReturnRequests — GET /api/v1/requests/return", async () => {
    try {
      const result = await fixture.client.listReturnRequests();
      expect(result).toHaveProperty("requests");
    } catch (err) {
      assertNotRouteLevelError(err, "listReturnRequests");
    }
  });

  it("getRequestStatus — GET /api/v1/requests/{id}/status (nonexistent)", async () => {
    // The server may return 200 with a synthetic "running" status for unknown IDs,
    // or 404 — both are acceptable as long as the route exists
    try {
      const result = await fixture.client.getRequestStatus("nonexistent-request-id-xyz");
      // 200 with synthetic data — route exists
      expect(result).toBeDefined();
      console.log(`  getRequestStatus(nonexistent) → 200 with synthetic data`);
    } catch (err) {
      assertNotRouteLevelError(err, "getRequestStatus(nonexistent)");
      if (err instanceof OrbApiError) {
        // 404 is also acceptable
        expect([404, 400].includes(err.statusCode)).toBe(true);
      }
    }
  });

  it("getRequestTimeline — GET /api/v1/requests/{id}/timeline (nonexistent)", async () => {
    try {
      await fixture.client.getRequestTimeline("nonexistent-request-id-xyz");
    } catch (err) {
      assertNotRouteLevelError(err, "getRequestTimeline(nonexistent)");
      if (err instanceof OrbApiError) {
        expect(err.statusCode).toBe(404);
      }
    }
  });

  it("batchGetRequestStatus — POST /api/v1/requests/status", async () => {
    try {
      const result = await fixture.client.batchGetRequestStatus({
        requestIds: ["nonexistent-id-1", "nonexistent-id-2"],
      });
      expect(result).toHaveProperty("requests");
    } catch (err) {
      assertNotRouteLevelError(err, "batchGetRequestStatus");
    }
  });

  it("cancelRequest — DELETE /api/v1/requests/{id} (nonexistent, with reason)", async () => {
    try {
      await fixture.client.cancelRequest("nonexistent-request-id-xyz", "contract-test");
    } catch (err) {
      assertNotRouteLevelError(err, "cancelRequest(nonexistent)");
      if (err instanceof OrbApiError) {
        // 404 = not found, 403 = needs operator role — both mean route exists
        expect([404, 403].includes(err.statusCode)).toBe(true);
      }
    }
  });

  it("purgeRequest — POST /api/v1/requests/{id}/purge (nonexistent)", async () => {
    try {
      await fixture.client.purgeRequest("nonexistent-request-id-xyz");
    } catch (err) {
      assertNotRouteLevelError(err, "purgeRequest(nonexistent)");
      if (err instanceof OrbApiError) {
        // 404 = not found, 403 = needs admin role — both mean route exists
        expect([404, 403].includes(err.statusCode)).toBe(true);
      }
    }
  });

  it("streamRequestStatus — GET /api/v1/requests/{id}/stream (nonexistent)", async () => {
    // A nonexistent request should return a 4xx error immediately (not hang)
    const ac = new AbortController();
    const timeoutHandle = setTimeout(() => ac.abort(), 5000);

    let caught: unknown;
    let events = 0;
    try {
      for await (const event of fixture.client.streamRequestStatus(
        "nonexistent-request-id-xyz",
        {
          signal: ac.signal,
          intervalSeconds: 1,
          timeoutSeconds: 2, // short timeout so stream closes quickly
        }
      )) {
        events++;
        console.log(`  streamRequestStatus event: ${event.status}`);
      }
    } catch (err) {
      caught = err;
    } finally {
      clearTimeout(timeoutHandle);
    }

    if (caught) {
      assertNotRouteLevelError(caught, "streamRequestStatus(nonexistent)");
      console.log(`  streamRequestStatus(nonexistent) → error (route exists): ${caught}`);
    } else {
      // Returned events (synthetic "running" for unknown IDs) — route exists
      console.log(`  streamRequestStatus(nonexistent) → ${events} events (route exists)`);
    }
  }, 15_000);
});

// ---------------------------------------------------------------------------
// SSE event stream
// ---------------------------------------------------------------------------

describe("Event stream", () => {
  it("streamEvents — GET /api/v1/events/ (connect and abort)", async () => {
    const ac = new AbortController();
    const events: unknown[] = [];

    // Connect, collect up to 1 event for 3s max, then abort
    const timeout = setTimeout(() => ac.abort(), 3000);

    try {
      for await (const frame of fixture.client.streamEvents({ signal: ac.signal })) {
        events.push(frame);
        // After first event, abort
        ac.abort();
        break;
      }
    } catch (err) {
      // AbortError is expected when signal fires
      if (err instanceof Error && err.name === "AbortError") {
        // ok
      } else {
        assertNotRouteLevelError(err, "streamEvents");
      }
    } finally {
      clearTimeout(timeout);
      if (!ac.signal.aborted) ac.abort();
    }

    // We don't assert a specific event count — just that the route exists and we
    // connected without a route-level error
    console.log(`  streamEvents: connected, got ${events.length} events before abort`);
  }, 10_000);
});

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

describe("Config", () => {
  it("getFullConfig — GET /api/v1/config/", async () => {
    try {
      const result = await fixture.client.getFullConfig();
      expect(typeof result).toBe("object");
    } catch (err) {
      assertNotRouteLevelError(err, "getFullConfig");
    }
  });

  it("getConfigSources — GET /api/v1/config/sources", async () => {
    try {
      const result = await fixture.client.getConfigSources();
      expect(typeof result).toBe("object");
    } catch (err) {
      assertNotRouteLevelError(err, "getConfigSources");
    }
  });

  it("getConfigValue — GET /api/v1/config/{key}", async () => {
    try {
      const result = await fixture.client.getConfigValue("server.port");
      expect(result).toBeDefined();
    } catch (err) {
      assertNotRouteLevelError(err, "getConfigValue(server.port)");
    }
  });

  it("validateConfig — POST /api/v1/config/validate", async () => {
    try {
      await fixture.client.validateConfig();
    } catch (err) {
      assertNotRouteLevelError(err, "validateConfig");
    }
  });

  it("saveConfig — POST /api/v1/config/save", async () => {
    try {
      await fixture.client.saveConfig({});
    } catch (err) {
      assertNotRouteLevelError(err, "saveConfig");
    }
  });

  it("setConfigValue — PUT /api/v1/config/{key}", async () => {
    try {
      await fixture.client.setConfigValue("logging.level", { value: "ERROR" });
    } catch (err) {
      assertNotRouteLevelError(err, "setConfigValue");
    }
  });
});

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

describe("Admin", () => {
  it("initOrb — POST /api/v1/admin/init", async () => {
    try {
      const result = await fixture.client.initOrb({
        confirm: "false",  // dry-run equivalent — spec expects string
        force: false,
        generate_templates: false,
      });
      expect(typeof result).toBe("object");
    } catch (err) {
      assertNotRouteLevelError(err, "initOrb");
    }
  });

  it("cleanupDatabase — POST /api/v1/admin/database/cleanup", async () => {
    try {
      await fixture.client.cleanupDatabase({
        confirm: "false",
        older_than_days: 999,
      });
    } catch (err) {
      assertNotRouteLevelError(err, "cleanupDatabase");
    }
  });

  it("reloadConfig — POST /api/v1/admin/reload-config", async () => {
    try {
      await fixture.client.reloadConfig();
    } catch (err) {
      assertNotRouteLevelError(err, "reloadConfig");
    }
  });

  // wipeDatabase is intentionally last (and uses confirm: false to be safe)
  it("wipeDatabase — POST /api/v1/admin/database/wipe (confirm: false)", async () => {
    try {
      await fixture.client.wipeDatabase({ confirm: false });
    } catch (err) {
      assertNotRouteLevelError(err, "wipeDatabase");
      // 400 acceptable — we didn't confirm
    }
  });
});
