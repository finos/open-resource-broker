// Layer 1: Subprocess Manager
package process

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const (
	startupPollInterval = 200 * time.Millisecond
	bgPollInterval      = 5 * time.Second
	bgPollTimeout       = 2 * time.Second
	unhealthyThreshold  = 3
)

// isPythonInterpreter reports whether binary names a Python interpreter (e.g.
// "python", "python3", "/opt/py/bin/python3.12") rather than the orb console
// script. The other SDK contract-test legs point their orb env var at
// .../bin/python, so orb must then be launched as `python -m orb`.
func isPythonInterpreter(binary string) bool {
	base := filepath.Base(binary)
	return base == "python" || strings.HasPrefix(base, "python")
}

// Config configures the managed ORB subprocess.
type Config struct {
	Binary string
	// ConfigPath, if set, is passed as the global `--config <path>` option
	// BEFORE the `server` subcommand. orb resolves its config directory from
	// the install layout when this is empty; in a fresh checkout that directory
	// does not exist and `orb server start` exits 1 with "Configuration file not
	// found", so callers running against a bare environment must supply one.
	ConfigPath   string
	Args         []string
	Env          []string
	SocketPath   string
	Port         int
	StartTimeout time.Duration
	StopTimeout  time.Duration
	HealthURL    string // override for testing; derived from Port/SocketPath if empty
}

// Manager starts and monitors an ORB subprocess.
type Manager struct {
	cfg             Config
	cmd             *exec.Cmd
	healthy         atomic.Bool
	consecutiveFail atomic.Int32
	stopCh          chan struct{}
	stopOnce        sync.Once
	httpClient      *http.Client
	logger          *slog.Logger
	waitCh          chan error // receives the single cmd.Wait() result
}

// newHealthClient returns an HTTP client suitable for health polling.
// When cfg.SocketPath is set it dials via UDS; otherwise plain TCP.
func newHealthClient(cfg Config) *http.Client {
	if cfg.SocketPath != "" {
		return &http.Client{
			Timeout: bgPollTimeout,
			Transport: &http.Transport{
				DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
					return (&net.Dialer{}).DialContext(ctx, "unix", cfg.SocketPath)
				},
			},
		}
	}
	return &http.Client{Timeout: bgPollTimeout}
}

// New creates a new Manager. Call Start to launch the process.
func New(cfg Config) *Manager {
	if cfg.Binary == "" {
		cfg.Binary = "orb"
	}
	if cfg.Port == 0 {
		cfg.Port = 8000
	}
	if cfg.StartTimeout == 0 {
		cfg.StartTimeout = 30 * time.Second
	}
	if cfg.StopTimeout == 0 {
		cfg.StopTimeout = 10 * time.Second
	}
	return &Manager{
		cfg:        cfg,
		stopCh:     make(chan struct{}),
		httpClient: newHealthClient(cfg),
		logger:     slog.Default(),
	}
}

// Start launches the ORB subprocess and waits for it to become healthy.
func (m *Manager) Start(ctx context.Context) error {
	binary := m.cfg.Binary
	// `--config` is a GLOBAL orb option and must precede the `server`
	// subcommand — passing it after `server start` is rejected as an unknown
	// argument. Build the global-option prefix once and reuse it for both the
	// direct-binary and the `python -m orb` invocation.
	var globalOpts []string
	if m.cfg.ConfigPath != "" {
		globalOpts = []string{"--config", m.cfg.ConfigPath}
	}
	// `orb server start` is the lifecycle command. --foreground keeps the
	// process in the current process tree (we manage the lifecycle from Go,
	// not via the ORB daemon). --api-only skips the embedded UI so the
	// subprocess is purely the REST + IPC surface this SDK talks to.
	subCmd := append([]string{"server", "start", "--foreground", "--api-only"}, m.cfg.Args...)

	// Two invocation shapes:
	//   orb    <global-opts> server start ...          (console script)
	//   python -m orb <global-opts> server start ...   (interpreter)
	// Choose based on the binary: an interpreter (e.g. an ORB_BINARY pointing at
	// .../bin/python, as the other SDK legs use) must go through `-m orb`.
	orbArgs := func() []string { return append(append([]string{}, globalOpts...), subCmd...) }
	pyArgs := func() []string { return append(append([]string{"-m", "orb"}, globalOpts...), subCmd...) }

	var args []string
	if isPythonInterpreter(binary) {
		args = pyArgs()
	} else {
		args = orbArgs()
	}

	// Resolve binary; fall back to python -m orb if not found in PATH.
	if _, err := exec.LookPath(binary); err != nil {
		if _, pyErr := exec.LookPath("python"); pyErr == nil {
			binary = "python"
		} else if _, pyErr := exec.LookPath("python3"); pyErr == nil {
			binary = "python3"
		} else {
			return fmt.Errorf("process: %q not found in PATH and python/python3 not available", m.cfg.Binary)
		}
		args = pyArgs()
	}

	if m.cfg.SocketPath != "" {
		args = append(args, "--socket-path", m.cfg.SocketPath)
	} else {
		args = append(args, "--port", fmt.Sprintf("%d", m.cfg.Port))
	}

	// Use background context for the command — the startup ctx is only for
	// health polling. If we used ctx here, the process would be killed when
	// the startup timeout context is cancelled after Start() returns.
	m.cmd = exec.CommandContext(context.Background(), binary, args...)
	m.cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if len(m.cfg.Env) > 0 {
		m.cmd.Env = m.cfg.Env
	}

	if err := m.cmd.Start(); err != nil {
		return fmt.Errorf("process: starting %q: %w", binary, err)
	}

	m.logger.Info("orb process started", "pid", m.cmd.Process.Pid, "binary", binary, "port", m.cfg.Port)

	// Reap the process in the background so we can detect a premature exit
	// during startup (fail fast with the exit code) instead of polling health
	// for the full StartTimeout on a process that is already dead. cmd.Wait may
	// only be called once, so the result is fanned out via this buffered channel
	// and consumed by whoever needs it (startup loop, Stop, or monitor).
	m.waitCh = make(chan error, 1)
	go func() { m.waitCh <- m.cmd.Wait() }()

	// Wait for healthy
	deadline := time.Now().Add(m.cfg.StartTimeout)
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			m.kill()
			return ctx.Err()
		case waitErr := <-m.waitCh:
			// Process exited before becoming healthy.
			m.kill()
			return fmt.Errorf("process: orb exited during startup before becoming healthy: %w", waitErr)
		case <-time.After(startupPollInterval):
		}
		if m.pollHealth() {
			m.healthy.Store(true)
			m.logger.Info("orb process healthy", "pid", m.cmd.Process.Pid)
			go m.monitor()
			return nil
		}
	}

	m.kill()
	return fmt.Errorf("process: orb did not become healthy within %s", m.cfg.StartTimeout)
}

// Stop sends SIGTERM and waits for the process to exit.
func (m *Manager) Stop() error {
	m.stopOnce.Do(func() { close(m.stopCh) })
	m.healthy.Store(false)

	if m.cmd == nil || m.cmd.Process == nil {
		return nil
	}

	// SIGTERM first
	if err := m.cmd.Process.Signal(syscall.SIGTERM); err != nil {
		m.kill()
		return nil
	}

	// cmd.Wait() is already running in the background goroutine started by
	// Start(); consume its result here rather than calling Wait() again (which
	// panics). If the process does not exit within StopTimeout, SIGKILL the
	// whole group and wait for the reaper to observe the exit.
	select {
	case <-m.waitCh:
	case <-time.After(m.cfg.StopTimeout):
		m.kill()
		<-m.waitCh
	}
	return nil
}

// Healthy reports whether the managed process is currently healthy.
func (m *Manager) Healthy() bool {
	return m.healthy.Load()
}

func (m *Manager) monitor() {
	ticker := time.NewTicker(bgPollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-m.stopCh:
			return
		case waitErr := <-m.waitCh:
			// Process exited out from under us — mark unhealthy immediately
			// rather than waiting for health polls to fail.
			m.healthy.Store(false)
			m.logger.Warn("orb process exited unexpectedly", "error", waitErr)
			return
		case <-ticker.C:
			if m.pollHealth() {
				m.consecutiveFail.Store(0)
				if !m.healthy.Load() {
					m.healthy.Store(true)
					m.logger.Info("orb process recovered")
				}
			} else {
				n := m.consecutiveFail.Add(1)
				if n >= unhealthyThreshold {
					m.healthy.Store(false)
					m.logger.Warn("orb process marked unhealthy", "consecutive_failures", n)
				}
			}
		}
	}
}

func (m *Manager) pollHealth() bool {
	url := m.cfg.HealthURL
	if url == "" {
		if m.cfg.SocketPath != "" {
			url = "http://localhost/health" // host ignored by UDS dialer
		} else {
			url = fmt.Sprintf("http://localhost:%d/health", m.cfg.Port)
		}
	}
	resp, err := m.httpClient.Get(url)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUnauthorized {
		m.logger.Warn("orb /health returned 401 — ensure /health is in excluded_paths")
		return false
	}
	if resp.StatusCode != http.StatusOK {
		return false
	}
	var body struct {
		Status string `json:"status"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return false
	}
	return body.Status == "healthy" || body.Status == "degraded"
}

func (m *Manager) kill() {
	if m.cmd != nil && m.cmd.Process != nil {
		// Kill the entire process group
		syscall.Kill(-m.cmd.Process.Pid, syscall.SIGKILL)
	}
}
