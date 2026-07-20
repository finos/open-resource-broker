// Package testutil provides helpers for integration testing with a real ORB process.
package testutil

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/orb"
)

// writeMinimalConfig writes a minimal orb config into a fresh temp dir and
// returns its path. This mirrors the config the other SDKs' contract tests
// spawn orb with (auth: none, json storage, aws-stub provider, ERROR logging).
//
// It is REQUIRED in CI: when orb is pip-installed outside a virtualenv it
// resolves its config directory from the repo layout (the checkout's
// pyproject.toml), which contains no config.json, so a bare `orb server start`
// exits 1 with "Configuration file not found". Passing an explicit --config
// gives orb everything it needs to boot in a clean environment.
func writeMinimalConfig(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	cfg := map[string]any{
		"version":   "2.0.0",
		"scheduler": map[string]any{"type": "default"},
		"provider": map[string]any{
			"providers": []any{
				map[string]any{
					"name":    "aws-stub",
					"type":    "aws",
					"enabled": true,
					"config":  map[string]any{"region": "us-east-1"},
				},
			},
		},
		"storage": map[string]any{"type": "json"},
		"server": map[string]any{
			"host":        "127.0.0.1",
			"port":        19997,
			"working_dir": dir,
			"pid_file":    filepath.Join(dir, "orb-server.pid"),
		},
		"auth":    map[string]any{"type": "none"},
		"logging": map[string]any{"level": "ERROR"},
	}
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		t.Fatalf("testutil.StartORB: marshal config: %v", err)
	}
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatalf("testutil.StartORB: write config: %v", err)
	}
	return path
}

// StartORB starts a real ORB process on a Unix domain socket and returns a connected client.
// The process is stopped automatically when the test ends via t.Cleanup.
// The orb binary is taken from the ORB_BINARY env var when set (e.g. a
// pip-installed interpreter path in CI), falling back to "orb" on PATH.
// Only runs when the "integration" build tag is set.
func StartORB(t *testing.T) *orb.Client {
	t.Helper()
	socketPath := fmt.Sprintf("/tmp/orb-test-%d.sock", time.Now().UnixNano())
	binary := os.Getenv("ORB_BINARY")
	if binary == "" {
		binary = "orb"
	}
	c, err := orb.NewClient(
		orb.WithManagedProcess(orb.ProcessConfig{
			Binary:       binary,
			ConfigPath:   writeMinimalConfig(t),
			SocketPath:   socketPath,
			StartTimeout: 30 * time.Second,
			StopTimeout:  10 * time.Second,
		}),
		orb.WithAuth(orb.WithNoAuth()),
	)
	if err != nil {
		t.Fatalf("testutil.StartORB: %v", err)
	}
	t.Cleanup(func() {
		if err := c.Close(); err != nil {
			t.Logf("testutil.StartORB cleanup: %v", err)
		}
	})
	return c
}

// WaitForStatus polls GetRequest until the request reaches the given status or ctx expires.
func WaitForStatus(ctx context.Context, c *orb.Client, requestID, status string) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
		req, err := c.GetRequest(ctx, requestID)
		if err != nil {
			return err
		}
		if req.Status == status {
			return nil
		}
	}
}
