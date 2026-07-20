/**
 * Layer 1: Subprocess Manager
 *
 * Spawns and supervises a local ORB process, waits for it to become healthy,
 * and stops it cleanly.
 *
 * Mirrors sdk/go/internal/process/manager.go behaviour exactly:
 *   - Binary path + args + env
 *   - Poll /health via UDS (when socketPath set) or TCP until healthy
 *   - Background health-check loop: unhealthy after N consecutive failures
 *   - Graceful stop: SIGTERM → wait → SIGKILL fallback
 *
 * The ORB server is started with:
 *   orb server start --foreground --api-only [--socket-path <sock>|--port <port>]
 */

import { spawn, type ChildProcess } from "child_process";
import { request as undiciRequest, Agent } from "undici";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

const STARTUP_POLL_INTERVAL_MS = 200;
const BG_POLL_INTERVAL_MS = 5_000;
const BG_POLL_TIMEOUT_MS = 2_000;
const UNHEALTHY_THRESHOLD = 3;

export interface ProcessConfig {
  /** Path to the orb binary (default: "orb") */
  binary?: string;
  /** Extra args after "server start --foreground --api-only" */
  args?: string[];
  /** Additional environment variables (merged with process.env) */
  env?: Record<string, string>;
  /** UNIX socket path (if set, UDS transport is used for health checks) */
  socketPath?: string;
  /** TCP port to listen on (default 8000). Ignored if socketPath is set. */
  port?: number;
  /** Max time to wait for /health to return healthy (default 30s) */
  startTimeoutMs?: number;
  /** SIGTERM grace period before SIGKILL (default 10s) */
  stopTimeoutMs?: number;
  /** Override health URL (for testing) */
  healthUrl?: string;
  /** PYTHONPATH to inject (useful when running from source) */
  pythonPath?: string;
}

export class SubprocessManager {
  private readonly cfg: Required<Omit<ProcessConfig, "healthUrl" | "pythonPath">> &
    Pick<ProcessConfig, "healthUrl" | "pythonPath">;
  private proc: ChildProcess | null = null;
  private _healthy = false;
  private consecutiveFail = 0;
  private bgInterval: NodeJS.Timeout | null = null;
  private stopped = false;
  private udsAgent: Agent | null = null;
  /** Set by the "exit" handler if the process dies before becoming healthy. */
  private exited: { code: number | null; signal: NodeJS.Signals | null } | null = null;
  private stderrTail = "";

  constructor(cfg: ProcessConfig = {}) {
    this.cfg = {
      binary: cfg.binary ?? "orb",
      args: cfg.args ?? [],
      env: cfg.env ?? {},
      socketPath: cfg.socketPath ?? "",
      port: cfg.port ?? 8000,
      startTimeoutMs: cfg.startTimeoutMs ?? 30_000,
      stopTimeoutMs: cfg.stopTimeoutMs ?? 10_000,
      healthUrl: cfg.healthUrl,
      pythonPath: cfg.pythonPath,
    };
    if (this.cfg.socketPath) {
      this.udsAgent = new Agent({
        connect: { socketPath: this.cfg.socketPath },
        connections: 1,
      });
    }
  }

  async start(): Promise<void> {
    if (this.proc) throw new Error("SubprocessManager: already started");

    const { binary, args } = this.resolveCommand();

    // UDS and port args
    const socketArgs: string[] = [];
    if (this.cfg.socketPath) {
      socketArgs.push("--socket-path", this.cfg.socketPath);
    } else {
      socketArgs.push("--port", String(this.cfg.port));
    }

    const fullArgs = [
      "server",
      "start",
      "--foreground",
      "--api-only",
      ...socketArgs,
      ...this.cfg.args,
    ];

    const env: Record<string, string> = {
      ...process.env as Record<string, string>,
      ...this.cfg.env,
    };
    if (this.cfg.pythonPath) {
      env.PYTHONPATH = this.cfg.pythonPath;
    }

    this.proc = spawn(binary, args.length ? [...args, ...fullArgs] : fullArgs, {
      env,
      stdio: ["ignore", "pipe", "pipe"],
      // detached:true puts the child in its own process group so we can signal
      // the whole group (child + any descendants) on teardown, avoiding orphans.
      detached: true,
    });

    // Capture the tail of stderr so a premature-exit error can report why.
    this.proc.stderr?.on("data", (chunk: Buffer) => {
      this.stderrTail = (this.stderrTail + chunk.toString("utf8")).slice(-4096);
    });
    this.proc.stdout?.on("data", (_chunk: Buffer) => {});

    this.proc.on("exit", (code, signal) => {
      this._healthy = false;
      this.exited = { code, signal };
    });

    // Poll until healthy, failing fast if the process exits during startup.
    const deadline = Date.now() + this.cfg.startTimeoutMs;
    while (Date.now() < deadline) {
      await sleep(STARTUP_POLL_INTERVAL_MS);

      // Fail fast on premature exit rather than polling health for the full
      // startup timeout only to report a generic "not healthy".
      if (this.exited) {
        const { code, signal } = this.exited;
        const reason =
          signal !== null ? `signal ${signal}` : `exit code ${code}`;
        const stderr = this.stderrTail.trim();
        throw new Error(
          `SubprocessManager: orb process exited during startup (${reason})` +
            (stderr ? `: ${stderr}` : "")
        );
      }

      if (await this.pollHealth()) {
        this._healthy = true;
        this.startBgMonitor();
        return;
      }
    }

    this.kill();
    throw new Error(
      `SubprocessManager: orb did not become healthy within ${this.cfg.startTimeoutMs}ms`
    );
  }

  stop(): Promise<void> {
    this.stopped = true;
    this._healthy = false;
    if (this.bgInterval) {
      clearInterval(this.bgInterval);
      this.bgInterval = null;
    }
    if (this.udsAgent) {
      void this.udsAgent.close();
      this.udsAgent = null;
    }

    return new Promise((resolve) => {
      if (!this.proc || this.proc.exitCode !== null) {
        resolve();
        return;
      }

      const killTimer = setTimeout(() => {
        this.kill();
        resolve();
      }, this.cfg.stopTimeoutMs);

      this.proc.on("exit", () => {
        clearTimeout(killTimer);
        resolve();
      });

      // Graceful stop: SIGTERM the whole process group so any descendants
      // orb spawned are asked to shut down too, then escalate to SIGKILL.
      if (!this.signalGroup("SIGTERM")) {
        this.kill();
        resolve();
      }
    });
  }

  get healthy(): boolean {
    return this._healthy;
  }

  // ---------------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------------

  private resolveCommand(): { binary: string; args: string[] } {
    // An absolute or relative path — use directly (let spawn fail if missing).
    if (this.cfg.binary.startsWith("/") || this.cfg.binary.startsWith(".")) {
      return { binary: this.cfg.binary, args: [] };
    }

    // A bare command name — resolve against PATH. If found, use it.
    const resolved = findExecutable([this.cfg.binary]);
    if (resolved) return { binary: resolved, args: [] };

    // orb binary not on PATH — fall back to `python3 -m orb` / `python -m orb`.
    const py = findExecutable(["python3", "python"]);
    if (py) return { binary: py, args: ["-m", "orb"] };

    throw new Error(
      `SubprocessManager: '${this.cfg.binary}' not found on PATH and no python ` +
        `interpreter (python3/python) available for fallback`
    );
  }

  private healthUrl(): string {
    if (this.cfg.healthUrl) return this.cfg.healthUrl;
    if (this.cfg.socketPath) {
      return "http://localhost/health"; // host ignored by UDS agent
    }
    return `http://localhost:${this.cfg.port}/health`;
  }

  private async pollHealth(): Promise<boolean> {
    try {
      const url = this.healthUrl();
      const options = this.udsAgent
        ? { dispatcher: this.udsAgent, signal: AbortSignal.timeout(BG_POLL_TIMEOUT_MS) }
        : { signal: AbortSignal.timeout(BG_POLL_TIMEOUT_MS) };

      const { statusCode, body } = await undiciRequest(url, options);

      if (statusCode === 401) return false; // auth enabled — /health should be in excluded_paths
      if (statusCode !== 200) return false;

      const chunks: Buffer[] = [];
      for await (const chunk of body) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      }
      const json = JSON.parse(Buffer.concat(chunks).toString("utf8")) as {
        status: string;
      };
      return json.status === "healthy" || json.status === "degraded";
    } catch {
      return false;
    }
  }

  private startBgMonitor(): void {
    this.bgInterval = setInterval(() => {
      void this.pollHealth().then((ok) => {
        if (ok) {
          this.consecutiveFail = 0;
          if (!this._healthy) this._healthy = true;
        } else {
          this.consecutiveFail++;
          if (this.consecutiveFail >= UNHEALTHY_THRESHOLD) {
            this._healthy = false;
          }
        }
      });
    }, BG_POLL_INTERVAL_MS);

    // Don't keep the process alive just for the health monitor
    if (this.bgInterval.unref) this.bgInterval.unref();
  }

  private kill(): void {
    if (this.proc && this.proc.exitCode === null) {
      if (!this.signalGroup("SIGKILL")) {
        try {
          this.proc.kill("SIGKILL");
        } catch {
          // ignore
        }
      }
    }
  }

  /**
   * Signal the child's entire process group (child spawned with detached:true,
   * so its pgid == pid). Falls back to signalling just the child if the group
   * signal fails (e.g. process already reaped). Returns false only if we could
   * not signal at all, so the caller can escalate.
   */
  private signalGroup(sig: NodeJS.Signals): boolean {
    if (!this.proc || this.proc.pid === undefined) return false;
    try {
      // Negative PID targets the whole process group.
      process.kill(-this.proc.pid, sig);
      return true;
    } catch {
      // Group may not exist (single process, or already gone) — try the child.
      try {
        this.proc.kill(sig);
        return true;
      } catch {
        return false;
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Resolve the first of `names` that exists as an executable on PATH. Iterates
 * ALL candidate names (not just the first) across every PATH directory, and
 * returns null if none are found so callers can fall back.
 */
function findExecutable(names: string[]): string | null {
  const paths = (process.env.PATH ?? "").split(path.delimiter).filter(Boolean);
  for (const name of names) {
    for (const dir of paths) {
      const full = path.join(dir, name);
      try {
        fs.accessSync(full, fs.constants.X_OK);
        return full;
      } catch {
        // Not here — try the next directory, then the next name.
      }
    }
  }
  return null;
}

/**
 * Auto-generate a temp socket path for a managed ORB process.
 */
export function tempSocketPath(): string {
  return path.join(os.tmpdir(), `orb-${process.pid}-${Date.now()}.sock`);
}
