package orb

import (
	"net/http"
	"time"
)

// ProcessConfig configures the managed ORB subprocess.
type ProcessConfig struct {
	// Binary is the path to the orb executable (default: "orb").
	Binary string
	// ConfigPath, if set, is passed as the global "--config <path>" option
	// before the "server" subcommand. Supply this when running against a fresh
	// environment that has no orb config directory (e.g. CI), otherwise
	// "orb server start" exits with "Configuration file not found".
	ConfigPath string
	// Args are extra arguments passed after "server start --foreground --api-only"
	// (e.g. ["--port", "18080"]).
	Args []string
	// Env sets additional environment variables for the subprocess.
	Env []string
	// SocketPath, if set, passes --socket-path to ORB and uses UDS transport.
	SocketPath string
	// Port is the TCP port ORB listens on (default 8000). Ignored if SocketPath is set.
	Port int
	// StartTimeout is how long to wait for /health to return healthy (default 30s).
	StartTimeout time.Duration
	// StopTimeout is the SIGTERM grace period before SIGKILL (default 10s).
	StopTimeout time.Duration
}

// SchedulerType identifies which ORB scheduler backend is in use.
type SchedulerType string

const (
	// SchedulerDefault is the standard ORB scheduler (snake_case JSON).
	SchedulerDefault SchedulerType = "default"
	// SchedulerHostFactory is the HostFactory scheduler (camelCase JSON).
	SchedulerHostFactory SchedulerType = "hostfactory"
)

// WithScheduler sets the scheduler type. Use SchedulerHostFactory when the ORB
// server is running with --scheduler hostfactory.
func WithScheduler(s SchedulerType) Option {
	return func(c *config) { c.scheduler = s }
}

type config struct {
	baseURL       string
	auth          authOption
	timeout       time.Duration
	maxRetries    int
	baseTransport http.RoundTripper
	process       *ProcessConfig
	socketPath    string
	scheduler     SchedulerType
}

func defaultConfig() config {
	return config{
		baseURL:    "http://localhost:8000",
		auth:       noAuth{},
		timeout:    30 * time.Second,
		maxRetries: 3,
		scheduler:  SchedulerDefault,
	}
}

// Option configures a Client.
type Option func(*config)

// WithBaseURL sets the ORB server base URL (default: http://localhost:8000).
func WithBaseURL(u string) Option {
	return func(c *config) { c.baseURL = u }
}

// WithAuth sets the authentication strategy.
func WithAuth(a AuthOption) Option {
	return func(c *config) { c.auth = a }
}

// WithTimeout sets the HTTP client timeout (default: 30s).
func WithTimeout(d time.Duration) Option {
	return func(c *config) { c.timeout = d }
}

// WithHTTPClient sets a custom http.Client. Overrides WithTimeout and transport options.
func WithHTTPClient(hc *http.Client) Option {
	return func(c *config) { c.baseTransport = hc.Transport }
}

// WithMaxRetries sets the maximum number of retries for transient errors (default: 3).
func WithMaxRetries(n int) Option {
	return func(c *config) { c.maxRetries = n }
}

// WithManagedProcess configures the client to start and manage an ORB subprocess.
func WithManagedProcess(cfg ProcessConfig) Option {
	return func(c *config) { c.process = &cfg }
}

// WithUnixSocket configures the client to connect via a Unix domain socket.
// The ORB server must be started with --socket-path pointing to the same path.
func WithUnixSocket(path string) Option {
	return func(c *config) {
		c.socketPath = path
		c.baseURL = "http://localhost" // host is ignored by UDS dialer
		if c.process != nil {
			c.process.SocketPath = path
		}
	}
}

// StreamOption configures a StreamRequestStatus call.
type StreamOption func(*streamConfig)

type streamConfig struct {
	interval time.Duration
	timeout  time.Duration
}

func defaultStreamConfig() streamConfig {
	return streamConfig{
		interval: 2 * time.Second,
		timeout:  300 * time.Second,
	}
}

// WithSSEInterval sets the polling interval for the SSE stream (default: 2s).
func WithSSEInterval(d time.Duration) StreamOption {
	return func(c *streamConfig) { c.interval = d }
}

// WithSSETimeout sets the maximum duration for the SSE stream (default: 300s).
func WithSSETimeout(d time.Duration) StreamOption {
	return func(c *streamConfig) { c.timeout = d }
}
