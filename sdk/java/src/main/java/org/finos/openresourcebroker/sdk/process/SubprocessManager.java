// Layer 1: Subprocess Manager
package org.finos.openresourcebroker.sdk.process;

import java.io.*;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.UnixDomainSocketAddress;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.channels.SocketChannel;
import java.nio.channels.Channels;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.stream.Collectors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.logging.Logger;

/**
 * Manages the lifecycle of a local ORB subprocess.
 *
 * <p>Responsibilities:
 * <ul>
 *   <li>Spawn {@code orb server start --foreground --api-only [--socket-path <path>]}
 *   <li>Poll {@code /health} until healthy or timeout
 *   <li>Background health-check loop; mark unhealthy after N consecutive failures
 *   <li>Graceful stop: SIGTERM → wait → SIGKILL fallback
 * </ul>
 *
 * <p>Thread-safe; all state transitions use atomics.
 */
public class SubprocessManager {

    private static final Logger LOG = Logger.getLogger(SubprocessManager.class.getName());

    private static final Duration STARTUP_POLL_INTERVAL = Duration.ofMillis(200);
    private static final long BG_POLL_INTERVAL_S = 5;
    private static final Duration BG_POLL_TIMEOUT = Duration.ofSeconds(2);
    private static final int UNHEALTHY_THRESHOLD = 3;

    private final ProcessConfig config;
    private Process process;
    private final AtomicBoolean healthy = new AtomicBoolean(false);
    private final AtomicInteger consecutiveFail = new AtomicInteger(0);
    private ScheduledExecutorService scheduler;
    private volatile boolean stopped = false;

    public SubprocessManager(ProcessConfig config) {
        this.config = config;
    }

    /**
     * Start the ORB subprocess and wait for it to become healthy.
     *
     * @throws IOException if the process cannot be started
     * @throws InterruptedException if the calling thread is interrupted
     * @throws IllegalStateException if ORB does not become healthy within the timeout
     */
    public synchronized void start() throws IOException, InterruptedException {
        String binary = resolveBinary(config.getBinary());
        List<String> cmd = buildCommand(binary);

        LOG.info("Starting ORB: " + String.join(" ", cmd));

        ProcessBuilder pb = new ProcessBuilder(cmd);
        if (config.getEnv() != null) {
            pb.environment().putAll(config.getEnv());
        }
        // Redirect stderr to stdout so we can see errors
        pb.redirectErrorStream(false);
        pb.redirectOutput(ProcessBuilder.Redirect.DISCARD);
        pb.redirectError(ProcessBuilder.Redirect.DISCARD);

        process = pb.start();
        LOG.info("ORB process started, PID=" + process.pid());

        // Wait for healthy
        long deadline = System.currentTimeMillis() + config.getStartTimeout().toMillis();
        while (System.currentTimeMillis() < deadline) {
            Thread.sleep(STARTUP_POLL_INTERVAL.toMillis());
            if (!process.isAlive()) {
                throw new IllegalStateException("ORB process exited unexpectedly (exit=" +
                        process.exitValue() + ")");
            }
            if (pollHealth()) {
                healthy.set(true);
                LOG.info("ORB process healthy, PID=" + process.pid());
                startMonitor();
                return;
            }
        }

        process.destroyForcibly();
        throw new IllegalStateException(
                "ORB did not become healthy within " + config.getStartTimeout());
    }

    /**
     * Stop the managed process gracefully (SIGTERM then SIGKILL).
     */
    public synchronized void stop() {
        if (stopped) return;
        stopped = true;
        healthy.set(false);

        if (scheduler != null) {
            scheduler.shutdownNow();
        }

        if (process == null) return;

        // Snapshot descendants BEFORE terminating the parent, otherwise the
        // process tree is no longer walkable once the parent dies.
        List<ProcessHandle> descendants = process.descendants().collect(Collectors.toList());

        // SIGTERM the parent first, then its descendants, for a graceful stop.
        process.destroy();
        descendants.forEach(ProcessHandle::destroy);

        try {
            boolean exited = process.waitFor(
                    config.getStopTimeout().toSeconds(), TimeUnit.SECONDS);
            if (!exited) {
                // Escalate to SIGKILL on the parent and any surviving children so
                // no orphaned child process is left behind.
                process.destroyForcibly();
                descendants.forEach(ProcessHandle::destroyForcibly);
            } else {
                // Parent gone; force-kill any child that ignored SIGTERM.
                descendants.stream()
                        .filter(ProcessHandle::isAlive)
                        .forEach(ProcessHandle::destroyForcibly);
            }
        } catch (InterruptedException e) {
            process.destroyForcibly();
            descendants.forEach(ProcessHandle::destroyForcibly);
            Thread.currentThread().interrupt();
        }
    }

    /** Returns true if the managed process is currently healthy. */
    public boolean isHealthy() {
        return healthy.get();
    }

    /** Returns the PID of the managed process, or -1 if not started. */
    public long getPid() {
        return process != null ? process.pid() : -1L;
    }

    // ------------------------------------------------------------------
    // Internal
    // ------------------------------------------------------------------

    private List<String> buildCommand(String binary) {
        List<String> cmd = new ArrayList<>();
        cmd.add(binary);

        // Support python -m orb invocation
        if (binary.endsWith("python") || binary.endsWith("python3") || binary.endsWith("python3.exe")) {
            cmd.add("-m");
            cmd.add("orb");
        }

        // Base args
        if (config.getConfigPath() != null) {
            cmd.add("--config");
            cmd.add(config.getConfigPath());
        }
        cmd.add("server");
        cmd.add("start");
        cmd.add("--foreground");
        cmd.add("--api-only");

        if (config.getSocketPath() != null && !config.getSocketPath().isEmpty()) {
            cmd.add("--socket-path");
            cmd.add(config.getSocketPath());
        } else {
            cmd.add("--port");
            cmd.add(String.valueOf(config.getPort() > 0 ? config.getPort() : 8000));
        }

        if (config.getExtraArgs() != null) {
            cmd.addAll(config.getExtraArgs());
        }
        return cmd;
    }

    private String resolveBinary(String binary) {
        if (binary == null || binary.isEmpty()) binary = "orb";
        if (isOnPath(binary)) return binary;

        // Try python -m orb
        for (String py : new String[]{"python3", "python"}) {
            if (isOnPath(py)) return py;
        }
        throw new IllegalStateException(
                "Cannot find ORB binary '" + binary + "' in PATH and python/python3 not available");
    }

    private static boolean isOnPath(String binary) {
        String path = System.getenv("PATH");
        if (path == null) return false;
        for (String dir : path.split(File.pathSeparator)) {
            File f = new File(dir, binary);
            if (f.canExecute()) return true;
        }
        return false;
    }

    private boolean pollHealth() {
        try {
            String healthUrl;
            if (config.getSocketPath() != null && !config.getSocketPath().isEmpty()) {
                healthUrl = "http://localhost/health";
                return pollHealthUds(config.getSocketPath(), healthUrl);
            } else {
                int port = config.getPort() > 0 ? config.getPort() : 8000;
                healthUrl = "http://localhost:" + port + "/health";
                return pollHealthTcp(healthUrl);
            }
        } catch (Exception e) {
            return false;
        }
    }

    private boolean pollHealthUds(String socketPath, String url) {
        try {
            UnixDomainSocketAddress addr = UnixDomainSocketAddress.of(socketPath);
            try (SocketChannel ch = SocketChannel.open()) {
                ch.configureBlocking(true);
                // Set connect timeout via socket options
                try {
                    ch.connect(addr);
                } catch (IOException e) {
                    return false;
                }

                OutputStream out = Channels.newOutputStream(ch);
                InputStream in = Channels.newInputStream(ch);

                String req = "GET /health HTTP/1.1\r\nHost: localhost\r\nAccept: application/json\r\nConnection: close\r\n\r\n";
                out.write(req.getBytes(StandardCharsets.US_ASCII));
                out.flush();

                return parseHealthResponse(in);
            }
        } catch (Exception e) {
            return false;
        }
    }

    private boolean pollHealthTcp(String url) {
        try {
            URI uri = URI.create(url);
            java.net.Socket sock = new java.net.Socket();
            sock.connect(new InetSocketAddress(uri.getHost(),
                    uri.getPort() == -1 ? 80 : uri.getPort()), 2000);
            sock.setSoTimeout(2000);
            try (sock) {
                String req = "GET /health HTTP/1.1\r\nHost: " + uri.getHost() + "\r\nAccept: application/json\r\nConnection: close\r\n\r\n";
                sock.getOutputStream().write(req.getBytes(StandardCharsets.US_ASCII));
                sock.getOutputStream().flush();
                return parseHealthResponse(sock.getInputStream());
            }
        } catch (Exception e) {
            return false;
        }
    }

    private boolean parseHealthResponse(InputStream in) throws IOException {
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(in, StandardCharsets.UTF_8));

        // Status line
        String statusLine = reader.readLine();
        if (statusLine == null) return false;
        String[] parts = statusLine.split(" ", 3);
        if (parts.length < 2) return false;
        int code;
        try { code = Integer.parseInt(parts[1].trim()); }
        catch (NumberFormatException e) { return false; }

        if (code == 401) return false; // auth configured on /health
        if (code != 200) return false;

        // Skip headers
        String line;
        while ((line = reader.readLine()) != null && !line.isEmpty()) { }

        // Body
        StringBuilder body = new StringBuilder();
        char[] buf = new char[1024];
        int n;
        while ((n = reader.read(buf)) != -1) body.append(buf, 0, n);

        String b = body.toString().trim();
        return b.contains("\"healthy\"") || b.contains("\"degraded\"");
    }

    private void startMonitor() {
        scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "orb-health-monitor");
            t.setDaemon(true);
            return t;
        });
        scheduler.scheduleAtFixedRate(() -> {
            if (stopped) return;
            if (pollHealth()) {
                consecutiveFail.set(0);
                if (!healthy.get()) {
                    healthy.set(true);
                    LOG.info("ORB process recovered");
                }
            } else {
                int n = consecutiveFail.incrementAndGet();
                if (n >= UNHEALTHY_THRESHOLD) {
                    healthy.set(false);
                    LOG.warning("ORB process marked unhealthy after " + n + " failures");
                }
            }
        }, BG_POLL_INTERVAL_S, BG_POLL_INTERVAL_S, TimeUnit.SECONDS);
    }
}
