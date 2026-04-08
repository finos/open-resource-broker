// Package testutil provides helpers for integration testing with a real ORB process.
package testutil

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/awslabs/open-resource-broker/sdk/go/orb"
)

// StartORB starts a real ORB process on a Unix domain socket and returns a connected client.
// The process is stopped automatically when the test ends via t.Cleanup.
// Requires the "orb" binary to be in PATH.
// Only runs when the "integration" build tag is set.
func StartORB(t *testing.T) *orb.Client {
	t.Helper()
	socketPath := fmt.Sprintf("/tmp/orb-test-%d.sock", time.Now().UnixNano())
	c, err := orb.NewClient(
		orb.WithManagedProcess(orb.ProcessConfig{
			Binary:       "orb",
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
