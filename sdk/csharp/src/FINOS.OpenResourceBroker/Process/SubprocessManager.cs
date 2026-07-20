// Layer 1: Subprocess Manager
//
// Spawns and supervises a local ORB process via System.Diagnostics.Process,
// waits for it to become healthy, and stops it cleanly.
//
// Teardown mirrors the Go reference: SIGTERM the process first (so ORB can
// flush and release resources gracefully), wait up to StopTimeoutMs, then
// escalate to Kill(entireProcessTree: true) — which reaps the whole tree so no
// child is orphaned.  On non-Unix platforms (no SIGTERM) it goes straight to
// the tree kill.

using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace FINOS.OpenResourceBroker.Process;

/// <summary>Configuration for a managed ORB subprocess.</summary>
public sealed class ProcessConfig
{
    /// <summary>
    /// Path to the ORB binary or Python interpreter.
    /// Default: "orb". If "orb" is not on PATH, falls back to "python3 -m orb"
    /// (then "python -m orb").
    /// </summary>
    public string Binary { get; init; } = "orb";

    /// <summary>Extra arguments appended after "server start --foreground --api-only".</summary>
    public string[] ExtraArgs { get; init; } = [];

    /// <summary>Additional environment variables merged with the current process environment.</summary>
    public Dictionary<string, string> Env { get; init; } = [];

    /// <summary>UNIX socket path for UDS mode (required for UDS transport).</summary>
    public string? SocketPath { get; init; }

    /// <summary>TCP port when not using UDS (default: 8000).</summary>
    public int Port { get; init; } = 8000;

    /// <summary>Max milliseconds to wait for /health to return healthy (default: 45_000).</summary>
    public int StartTimeoutMs { get; init; } = 45_000;

    /// <summary>SIGTERM grace period before Kill (default: 10_000).</summary>
    public int StopTimeoutMs { get; init; } = 10_000;

    /// <summary>PYTHONPATH to inject (useful when running from source tree).</summary>
    public string? PythonPath { get; init; }
}

/// <summary>
/// Manages the lifecycle of a local ORB subprocess.
/// </summary>
internal sealed class SubprocessManager : IAsyncDisposable
{
    private const int PollIntervalMs = 250;
    private const int BgPollIntervalMs = 5_000;
    private const int BgPollTimeoutMs = 2_000;
    private const int UnhealthyThreshold = 3;
    private const int Sigterm = 15; // POSIX SIGTERM

    private readonly ProcessConfig _cfg;
    private System.Diagnostics.Process? _proc;
    // Written from the background monitor Task and the Exited handler; read from
    // the calling thread.  Marked volatile to guarantee cross-thread visibility.
    private volatile bool _healthy;
    private int _consecutiveFail;
    private CancellationTokenSource? _bgCts;
    private Task? _bgTask;
    private volatile bool _stopped;

    public bool Healthy => _healthy;

    public SubprocessManager(ProcessConfig cfg) => _cfg = cfg;

    public async Task StartAsync(CancellationToken ct = default)
    {
        if (_proc != null) throw new InvalidOperationException("SubprocessManager: already started");

        var (binary, prefixArgs) = ResolveCommand();
        var socketArgs = _cfg.SocketPath != null
            ? new[] { "--socket-path", _cfg.SocketPath }
            : new[] { "--port", _cfg.Port.ToString() };

        var startInfo = new ProcessStartInfo
        {
            FileName = binary,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };

        // Use ArgumentList (one element per arg) so .NET applies OS-correct
        // quoting/escaping — avoids the naive "quote only if it contains a space"
        // string concatenation that mis-handles quotes/tabs/special chars.
        foreach (var a in prefixArgs) startInfo.ArgumentList.Add(a);
        startInfo.ArgumentList.Add("server");
        startInfo.ArgumentList.Add("start");
        startInfo.ArgumentList.Add("--foreground");
        startInfo.ArgumentList.Add("--api-only");
        foreach (var a in socketArgs) startInfo.ArgumentList.Add(a);
        foreach (var a in _cfg.ExtraArgs) startInfo.ArgumentList.Add(a);

        foreach (var (k, v) in _cfg.Env)
            startInfo.Environment[k] = v;

        if (!string.IsNullOrEmpty(_cfg.PythonPath))
            startInfo.Environment["PYTHONPATH"] = _cfg.PythonPath;

        _proc = new System.Diagnostics.Process { StartInfo = startInfo, EnableRaisingEvents = true };
        _proc.Exited += (_, _) => { if (!_stopped) _healthy = false; };
        _proc.Start();

        // Drain stdout/stderr to prevent blocking
        _ = _proc.StandardOutput.ReadToEndAsync();
        _ = _proc.StandardError.ReadToEndAsync();

        // Poll until healthy
        var deadline = DateTimeOffset.UtcNow.AddMilliseconds(_cfg.StartTimeoutMs);
        using var client = BuildHealthClient();

        while (DateTimeOffset.UtcNow < deadline && !ct.IsCancellationRequested)
        {
            await Task.Delay(PollIntervalMs, ct).ConfigureAwait(false);

            // Fail fast if the process died during startup — don't poll health
            // for the full timeout when there is nothing left to become healthy.
            if (_proc.HasExited)
            {
                var exitCode = _proc.ExitCode;
                throw new OrbUnavailableException(
                    $"ORB process exited during startup with code {exitCode}");
            }

            if (await PollHealthAsync(client).ConfigureAwait(false))
            {
                _healthy = true;
                StartBgMonitor();
                return;
            }
        }

        KillProcess();
        throw new OrbUnavailableException(
            $"ORB process did not become healthy within {_cfg.StartTimeoutMs}ms");
    }

    public async Task StopAsync(CancellationToken ct = default)
    {
        _stopped = true;
        _healthy = false;

        if (_bgCts != null)
        {
            await _bgCts.CancelAsync().ConfigureAwait(false);
            if (_bgTask != null)
            {
                try { await _bgTask.ConfigureAwait(false); }
                catch { /* expected cancellation */ }
            }
            _bgCts.Dispose();
            _bgCts = null;
        }

        if (_proc == null || _proc.HasExited) return;

        // Graceful stop: SIGTERM first (Unix), then wait for a clean exit so ORB
        // can flush state and release resources.
        var termSignalled = TrySigterm(_proc);

        try
        {
            using var cts = new CancellationTokenSource(_cfg.StopTimeoutMs);
            await _proc.WaitForExitAsync(cts.Token).ConfigureAwait(false);
        }
        catch
        {
            // Timed out (or SIGTERM was unavailable) — escalate to a forceful
            // tree kill so no descendant is left orphaned.
            try { _proc.Kill(entireProcessTree: true); } catch { }
        }

        // If SIGTERM was never delivered (non-Unix), ensure the tree is gone.
        if (!termSignalled && !_proc.HasExited)
        {
            try { _proc.Kill(entireProcessTree: true); } catch { }
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (!_stopped) await StopAsync().ConfigureAwait(false);
        _proc?.Dispose();
    }

    // ---------------------------------------------------------------------------
    // Private helpers
    // ---------------------------------------------------------------------------

    [DllImport("libc", SetLastError = true, EntryPoint = "kill")]
    private static extern int PosixKill(int pid, int sig);

    // Send SIGTERM on Unix.  Returns true if the signal was delivered (so the
    // caller waits for a graceful exit); false on Windows / on error (the caller
    // falls back to the forceful tree kill).
    private static bool TrySigterm(System.Diagnostics.Process proc)
    {
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return false;
        try
        {
            return PosixKill(proc.Id, Sigterm) == 0;
        }
        catch
        {
            return false;
        }
    }

    private (string binary, string[] prefixArgs) ResolveCommand()
    {
        // Explicit path (absolute or relative) — use as-is.
        if (_cfg.Binary != "orb" && (_cfg.Binary.StartsWith('/') || _cfg.Binary.StartsWith('.')))
            return (_cfg.Binary, []);

        // A configured non-default binary name: trust it if it resolves on PATH.
        if (_cfg.Binary != "orb")
        {
            if (IsOnPath(_cfg.Binary)) return (_cfg.Binary, []);
            throw new OrbUnavailableException(
                $"ORB binary '{_cfg.Binary}' not found on PATH");
        }

        // Default: prefer the 'orb' binary on PATH; otherwise fall back to
        // running the module via python3 / python (consistent with the other
        // SDKs' python fallback).
        if (IsOnPath("orb")) return ("orb", []);
        foreach (var py in new[] { "python3", "python" })
            if (IsOnPath(py)) return (py, new[] { "-m", "orb" });

        throw new OrbUnavailableException(
            "ORB binary 'orb' not found on PATH and no python interpreter " +
            "(python3/python) available for the 'python -m orb' fallback");
    }

    private static bool IsOnPath(string name)
    {
        // An explicit path is handled by the caller; here 'name' is a bare
        // command looked up across the PATH directories.
        var pathVar = Environment.GetEnvironmentVariable("PATH") ?? "";
        var isWindows = RuntimeInformation.IsOSPlatform(OSPlatform.Windows);
        foreach (var dir in pathVar.Split(Path.PathSeparator))
        {
            if (string.IsNullOrEmpty(dir)) continue;
            var full = Path.Combine(dir, name);
            if (File.Exists(full)) return true;
            if (isWindows && (File.Exists(full + ".exe") || File.Exists(full + ".cmd") || File.Exists(full + ".bat")))
                return true;
        }
        return false;
    }

    private HttpClient BuildHealthClient()
    {
        if (_cfg.SocketPath != null)
        {
            var handler = Transport.UdsHttpHandlerFactory.Create(_cfg.SocketPath);
            return new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        }
        return new HttpClient { BaseAddress = new Uri($"http://localhost:{_cfg.Port}") };
    }

    private async Task<bool> PollHealthAsync(HttpClient client)
    {
        try
        {
            using var cts = new CancellationTokenSource(BgPollTimeoutMs);
            var resp = await client.GetAsync("/health", cts.Token).ConfigureAwait(false);
            if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized) return false;
            if (!resp.IsSuccessStatusCode) return false;
            var json = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            var doc = JsonDocument.Parse(json);
            var status = doc.RootElement.GetProperty("status").GetString() ?? "";
            return status is "healthy" or "degraded";
        }
        catch { return false; }
    }

    private void StartBgMonitor()
    {
        _bgCts = new CancellationTokenSource();
        var ct = _bgCts.Token;
        var client = BuildHealthClient();

        _bgTask = Task.Run(async () =>
        {
            try
            {
                while (!ct.IsCancellationRequested)
                {
                    await Task.Delay(BgPollIntervalMs, ct).ConfigureAwait(false);
                    var ok = await PollHealthAsync(client).ConfigureAwait(false);
                    if (ok)
                    {
                        _consecutiveFail = 0;
                        if (!_healthy) _healthy = true;
                    }
                    else
                    {
                        _consecutiveFail++;
                        if (_consecutiveFail >= UnhealthyThreshold) _healthy = false;
                    }
                }
            }
            catch (OperationCanceledException) { }
            finally { client.Dispose(); }
        }, ct);
    }

    private void KillProcess()
    {
        if (_proc != null && !_proc.HasExited)
        {
            try { _proc.Kill(entireProcessTree: true); } catch { }
        }
    }
}
