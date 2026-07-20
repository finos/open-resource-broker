// OrbFixture — spawns a real ORB process over a UNIX socket for contract tests.
//
// ORB is started with:
//   python -m orb --config <tmp-config.json> server start --foreground --api-only --socket-path <sock>
//
// The config uses:
//   - auth: none
//   - storage: json
//   - provider: aws with stub credentials (region us-east-1)
//   - scheduler: default
//   - logging: ERROR

using System.Diagnostics;
using System.Net.Sockets;
using System.Text.Json;
using FINOS.OpenResourceBroker;
using FINOS.OpenResourceBroker.Transport;
using Xunit;

namespace ContractTests;

/// <summary>
/// xUnit class fixture that spawns a real ORB process and provides a connected OrbClient.
/// </summary>
public sealed class OrbFixture : IAsyncLifetime
{
    private const int StartTimeoutMs = 60_000;
    private const int PollIntervalMs = 300;

    // Path to the Python interpreter and ORB source tree.
    // These are set from env vars (ORB_PYTHON, ORB_SRC) or fall back to a
    // PATH-walking search over "python3" then "python", matching the portability
    // approach used by the Java fixture (OrbContractTest.java).
    // CI always sets ORB_PYTHON explicitly; the PATH walk is for local dev
    // convenience on macOS, Linux, and Windows dev environments.
    private static readonly string OrbPython =
        Environment.GetEnvironmentVariable("ORB_PYTHON")
        ?? FindPythonOnPath()
        ?? "python3";

    private static string? FindPythonOnPath()
    {
        var pathVar = Environment.GetEnvironmentVariable("PATH") ?? "";
        var dirs = pathVar.Split(Path.PathSeparator);
        foreach (var candidate in new[] { "python3", "python" })
        {
            foreach (var dir in dirs)
            {
                if (string.IsNullOrEmpty(dir)) continue;
                var fullPath = Path.Combine(dir, candidate);
                // On Windows the executable may have a .exe extension.
                if (File.Exists(fullPath)) return fullPath;
                if (File.Exists(fullPath + ".exe")) return fullPath + ".exe";
            }
        }
        return null;
    }

    // OrbSrc is only used if ORB_SRC is set (orb installed via pip doesn't need PYTHONPATH).
    private static readonly string? OrbSrc =
        Environment.GetEnvironmentVariable("ORB_SRC");

    private System.Diagnostics.Process? _proc;
    private string? _tmpDir;

    public OrbClient Client { get; private set; } = null!;
    public string SocketPath { get; private set; } = "";
    public int Pid => _proc?.Id ?? -1;

    public async Task InitializeAsync()
    {
        _tmpDir = Path.Combine(Path.GetTempPath(), "orb-contract-" + Guid.NewGuid().ToString("N")[..8]);
        Directory.CreateDirectory(_tmpDir);
        SocketPath = Path.Combine(_tmpDir, "orb.sock");
        var configPath = Path.Combine(_tmpDir, "config.json");

        var config = new
        {
            version = "2.0.0",
            scheduler = new { type = "default" },
            provider = new
            {
                providers = new[]
                {
                    new
                    {
                        name = "aws-stub",
                        type = "aws",
                        enabled = true,
                        config = new { region = "us-east-1" }
                    }
                }
            },
            storage = new { type = "json" },
            server = new
            {
                host = "127.0.0.1",
                port = 19996,
                working_dir = _tmpDir,
                pid_file = Path.Combine(_tmpDir, "orb-server.pid")
            },
            auth = new { type = "none" },
            logging = new { level = "ERROR" }
        };

        await File.WriteAllTextAsync(configPath, JsonSerializer.Serialize(config, new JsonSerializerOptions
        {
            WriteIndented = true,
        }));

        var startInfo = new ProcessStartInfo
        {
            FileName = OrbPython,
            Arguments = $"-m orb --config \"{configPath}\" server start --foreground --api-only --socket-path \"{SocketPath}\"",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        if (!string.IsNullOrEmpty(OrbSrc))
            startInfo.Environment["PYTHONPATH"] = OrbSrc;
        startInfo.Environment["ORB_LOG_LEVEL"] = "ERROR";

        _proc = new System.Diagnostics.Process { StartInfo = startInfo, EnableRaisingEvents = true };
        _proc.Start();
        _ = _proc.StandardOutput.ReadToEndAsync();
        _ = _proc.StandardError.ReadToEndAsync();

        // Wait for the socket to appear and /health to return OK
        var deadline = DateTimeOffset.UtcNow.AddMilliseconds(StartTimeoutMs);
        var healthy = false;

        while (DateTimeOffset.UtcNow < deadline)
        {
            await Task.Delay(PollIntervalMs);
            if (!File.Exists(SocketPath)) continue;

            try
            {
                var udsHandler = UdsHttpHandlerFactory.Create(SocketPath);
                using var httpClient = new HttpClient(udsHandler) { BaseAddress = new Uri("http://localhost") };
                using var cts = new CancellationTokenSource(2000);
                var resp = await httpClient.GetAsync("/health", cts.Token);
                if (resp.IsSuccessStatusCode)
                {
                    var json = await resp.Content.ReadAsStringAsync();
                    var doc = JsonDocument.Parse(json);
                    var status = doc.RootElement.GetProperty("status").GetString() ?? "";
                    if (status is "healthy" or "degraded")
                    {
                        healthy = true;
                        break;
                    }
                }
            }
            catch { /* not ready */ }
        }

        if (!healthy)
        {
            _proc.Kill(entireProcessTree: true);
            throw new Exception($"ORB did not become healthy within {StartTimeoutMs}ms (socket: {SocketPath})");
        }

        Client = await OrbClient.CreateAsync(new ClientConfig
        {
            SocketPath = SocketPath,
            Auth = FINOS.OpenResourceBroker.Auth.AuthOption.None,
            TimeoutMs = 15_000,
            Retry = new FINOS.OpenResourceBroker.Transport.RetryConfig
            {
                MaxRetries = 1,
                BaseDelayMs = 100,
            },
        });

        Console.WriteLine($"ORB started: PID={Pid}, socket={SocketPath}");
    }

    public async Task DisposeAsync()
    {
        await Client.DisposeAsync();

        if (_proc != null && !_proc.HasExited)
        {
            try { _proc.Kill(entireProcessTree: true); }
            catch { }

            try
            {
                using var cts = new CancellationTokenSource(5000);
                await _proc.WaitForExitAsync(cts.Token);
            }
            catch { }
        }

        _proc?.Dispose();

        if (_tmpDir != null && Directory.Exists(_tmpDir))
        {
            try { Directory.Delete(_tmpDir, recursive: true); }
            catch { }
        }

        Console.WriteLine("ORB stopped");
    }
}
