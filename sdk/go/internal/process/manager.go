package process

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os/exec"
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

// Config configures the managed ORB subprocess.
type Config struct {
	Binary       string
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
	// `orb server start` is the lifecycle command. --foreground keeps the
	// process in the current process tree (we manage the lifecycle from Go,
	// not via the ORB daemon). --api-only skips the embedded UI so the
	// subprocess is purely the REST + IPC surface this SDK talks to.
	baseArgs := []string{"server", "start", "--foreground", "--api-only"}
	args := append(append([]string{}, baseArgs...), m.cfg.Args...)

	// Resolve binary; fall back to python -m orb if not found in PATH
	if _, err := exec.LookPath(binary); err != nil {
		if _, pyErr := exec.LookPath("python"); pyErr == nil {
			binary = "python"
			args = append(append([]string{"-m", "orb"}, baseArgs...), m.cfg.Args...)
		} else if _, pyErr := exec.LookPath("python3"); pyErr == nil {
			binary = "python3"
			args = append(append([]string{"-m", "orb"}, baseArgs...), m.cfg.Args...)
		} else {
			return fmt.Errorf("process: %q not found in PATH and python/python3 not available", m.cfg.Binary)
		}
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

	// Wait for healthy
	deadline := time.Now().Add(m.cfg.StartTimeout)
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			m.kill()
			return ctx.Err()
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

	done := make(chan error, 1)
	go func() { done <- m.cmd.Wait() }()

	select {
	case <-done:
	case <-time.After(m.cfg.StopTimeout):
		m.kill()
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
