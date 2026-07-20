/**
 * Layer 1: Subprocess Manager
 *
 * Spawns and supervises a local ORB process, polls /health until healthy,
 * and stops it cleanly.
 *
 * Mirrors sdk/go/internal/process/manager.go behaviour:
 *   - Binary path + args + env
 *   - Poll /health via UDS or TCP until healthy or timeout
 *   - Background health-check loop: unhealthy after N consecutive failures
 *   - Graceful stop: SIGTERM -> wait -> SIGKILL fallback
 *   - Thread-safe via ReentrantLock + @Volatile
 */

package org.finos.openresourcebroker.sdk.process

import kotlinx.coroutines.*
import okhttp3.OkHttpClient
import okhttp3.Request
import org.finos.openresourcebroker.sdk.transport.UdsSocketFactory
import java.io.File
import java.time.Duration
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

private const val STARTUP_POLL_INTERVAL_MS = 200L
private const val BG_POLL_INTERVAL_MS = 5_000L
private const val BG_POLL_TIMEOUT_MS = 2_000L
private const val UNHEALTHY_THRESHOLD = 3

/**
 * Configuration for spawning a managed ORB subprocess.
 */
data class ProcessConfig(
    /** Path to the orb binary (default: "orb"). Accepts "python3 -m orb" style too. */
    val binary: String = "orb",
    /** Extra arguments appended after "server start --foreground --api-only" */
    val args: List<String> = emptyList(),
    /** Additional environment variables merged with system env */
    val env: Map<String, String> = emptyMap(),
    /** UNIX socket path; if set, UDS transport is used for health checks */
    val socketPath: String = "",
    /** TCP port (ignored when socketPath is set) */
    val port: Int = 8000,
    /** Max ms to wait for /health to return healthy (default 45s) */
    val startTimeoutMs: Long = 45_000L,
    /** SIGTERM grace period before SIGKILL (default 10s) */
    val stopTimeoutMs: Long = 10_000L,
    /** PYTHONPATH injection (useful when running from source) */
    val pythonPath: String = "",
)

/**
 * Manages an ORB subprocess lifecycle.
 */
class SubprocessManager(private val cfg: ProcessConfig) {

    private val lock = ReentrantLock()
    private var proc: Process? = null
    private val _healthy = AtomicBoolean(false)
    private val consecutiveFail = AtomicInteger(0)
    private val stopped = AtomicBoolean(false)
    private var bgJob: Job? = null

    /** True if the managed process is currently healthy. */
    val healthy: Boolean get() = _healthy.get()

    /**
     * Start the ORB subprocess and wait for it to become healthy.
     * Throws [IllegalStateException] if already started.
     * Throws [RuntimeException] if the process does not become healthy within the timeout.
     */
    suspend fun start() {
        lock.withLock {
            check(proc == null) { "SubprocessManager: already started" }

            val command = buildCommand()
            val fullEnv = buildEnvironment()

            val pb = ProcessBuilder(command)
                .redirectErrorStream(false)
                .also { builder ->
                    builder.environment().putAll(fullEnv)
                }

            proc = pb.start()

            // Drain stdout/stderr to prevent blocking
            val process = proc!!
            Thread {
                try { process.inputStream.copyTo(java.io.OutputStream.nullOutputStream()) } catch (_: Exception) {}
            }.also { it.isDaemon = true; it.start() }
            Thread {
                try { process.errorStream.copyTo(java.io.OutputStream.nullOutputStream()) } catch (_: Exception) {}
            }.also { it.isDaemon = true; it.start() }
        }

        // Poll until healthy (outside lock to allow cancellation)
        val deadline = System.currentTimeMillis() + cfg.startTimeoutMs
        while (System.currentTimeMillis() < deadline) {
            delay(STARTUP_POLL_INTERVAL_MS)
            if (!isRunning()) {
                throw RuntimeException("SubprocessManager: ORB process exited prematurely")
            }
            if (pollHealth()) {
                _healthy.set(true)
                startBgMonitor()
                return
            }
        }

        kill()
        throw RuntimeException(
            "SubprocessManager: orb did not become healthy within ${cfg.startTimeoutMs}ms"
        )
    }

    /**
     * Stop the managed subprocess gracefully.
     * Sends SIGTERM, waits for stopTimeoutMs, then sends SIGKILL.
     */
    fun stop() {
        stopped.set(true)
        _healthy.set(false)
        bgJob?.cancel()
        bgJob = null

        val process = lock.withLock { proc } ?: return

        // Snapshot descendants BEFORE terminating the parent, otherwise the
        // process tree is no longer walkable once the parent dies.
        val descendants = process.descendants().toList()

        // SIGTERM the parent first, then its descendants, for a graceful stop.
        process.destroy()
        descendants.forEach { it.destroy() }

        val exited = process.waitFor(cfg.stopTimeoutMs, TimeUnit.MILLISECONDS)
        if (!exited) {
            // Escalate to SIGKILL on the parent and any surviving children so no
            // orphaned child process is left behind.
            process.destroyForcibly()
            descendants.forEach { it.destroyForcibly() }
            process.waitFor(5, TimeUnit.SECONDS)
        } else {
            // Parent gone; force-kill any child that ignored SIGTERM.
            descendants.filter { it.isAlive }.forEach { it.destroyForcibly() }
        }
    }

    // ---------------------------------------------------------------------------
    // Private helpers
    // ---------------------------------------------------------------------------

    private fun isRunning(): Boolean {
        val p = lock.withLock { proc } ?: return false
        return p.isAlive
    }

    private fun buildCommand(): List<String> {
        val socketArgs = if (cfg.socketPath.isNotEmpty()) {
            listOf("--socket-path", cfg.socketPath)
        } else {
            listOf("--port", cfg.port.toString())
        }

        val (executable, prefixArgs) = resolveExecutable()

        val serverArgs = listOf("server", "start", "--foreground", "--api-only") +
                socketArgs + cfg.args

        return listOf(executable) + prefixArgs + serverArgs
    }

    /**
     * Resolve the executable to spawn, mirroring the Go/Java fallback:
     *   1. An absolute/relative path is used directly.
     *   2. A binary name found on PATH is used as-is.
     *   3. Otherwise fall back to python/python3 (`-m orb`) if available.
     *   4. If nothing is found, fail loud with a clear error.
     *
     * @return the executable and any prefix args (e.g. `-m orb` for python).
     */
    private fun resolveExecutable(): Pair<String, List<String>> {
        val binary = cfg.binary.ifEmpty { "orb" }

        // Explicit path — trust the caller.
        if (binary.startsWith("/") || binary.startsWith(".")) {
            val prefix = if (isPythonBinary(binary)) listOf("-m", "orb") else emptyList()
            return binary to prefix
        }

        // A python interpreter passed by name runs `-m orb`.
        if (isPythonBinary(binary)) {
            return binary to listOf("-m", "orb")
        }

        // Binary name resolvable on PATH — use as-is.
        if (isOnPath(binary)) {
            return binary to emptyList()
        }

        // Fall back to python/python3 -m orb.
        for (py in listOf("python3", "python")) {
            if (isOnPath(py)) {
                return py to listOf("-m", "orb")
            }
        }

        throw IllegalStateException(
            "Cannot find ORB binary '$binary' in PATH and python/python3 not available"
        )
    }

    private fun isPythonBinary(binary: String): Boolean =
        binary.endsWith("python") || binary.endsWith("python3") || binary.endsWith("python3.exe")

    private fun isOnPath(binary: String): Boolean {
        val path = System.getenv("PATH") ?: return false
        return path.split(File.pathSeparator).any { dir ->
            File(dir, binary).canExecute()
        }
    }

    private fun buildEnvironment(): Map<String, String> {
        val env = ProcessBuilder().environment().toMutableMap()
        env.putAll(cfg.env)
        if (cfg.pythonPath.isNotEmpty()) {
            env["PYTHONPATH"] = cfg.pythonPath
        }
        return env
    }

    private fun healthUrl(): String {
        return if (cfg.socketPath.isNotEmpty()) {
            "http://localhost/health"
        } else {
            "http://localhost:${cfg.port}/health"
        }
    }

    private fun makeHealthClient(): OkHttpClient {
        val builder = OkHttpClient.Builder()
            .connectTimeout(Duration.ofMillis(BG_POLL_TIMEOUT_MS))
            .readTimeout(Duration.ofMillis(BG_POLL_TIMEOUT_MS))
            .callTimeout(Duration.ofMillis(BG_POLL_TIMEOUT_MS))

        if (cfg.socketPath.isNotEmpty()) {
            builder.socketFactory(UdsSocketFactory(cfg.socketPath))
        }
        return builder.build()
    }

    private fun pollHealth(): Boolean {
        return try {
            val client = makeHealthClient()
            val req = Request.Builder().url(healthUrl()).get().build()
            client.newCall(req).execute().use { resp ->
                if (resp.code == 401) return false // auth enabled but /health open
                if (resp.code != 200) return false
                val body = resp.body?.string() ?: return false
                body.contains("\"healthy\"") || body.contains("\"degraded\"")
            }
        } catch (_: Exception) {
            false
        }
    }

    private fun kill() {
        val process = lock.withLock { proc } ?: return
        val descendants = process.descendants().toList()
        process.destroyForcibly()
        descendants.forEach { it.destroyForcibly() }
    }

    private fun startBgMonitor() {
        bgJob = CoroutineScope(Dispatchers.IO).launch {
            while (isActive && !stopped.get()) {
                delay(BG_POLL_INTERVAL_MS)
                if (stopped.get()) break
                val ok = pollHealth()
                if (ok) {
                    consecutiveFail.set(0)
                    _healthy.set(true)
                } else {
                    val fails = consecutiveFail.incrementAndGet()
                    if (fails >= UNHEALTHY_THRESHOLD) {
                        _healthy.set(false)
                    }
                }
            }
        }
    }
}

/**
 * Generate a unique temp socket path for a managed ORB process.
 */
fun tempSocketPath(): String =
    "${System.getProperty("java.io.tmpdir")}/orb-${ProcessHandle.current().pid()}-${System.currentTimeMillis()}.sock"
