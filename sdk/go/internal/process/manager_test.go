package process_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/internal/process"
)

func TestManagerMarksUnhealthyAfterThreeFailures(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	m := process.New(process.Config{
		Binary:       "true",
		HealthURL:    srv.URL,
		StartTimeout: 100 * time.Millisecond,
	})

	if m == nil {
		t.Fatal("expected non-nil manager")
	}
	if m.Healthy() {
		t.Fatal("expected unhealthy before start")
	}
}

func TestManagerHealthyServer(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "healthy"})
	}))
	defer srv.Close()

	m := process.New(process.Config{
		Binary:    "true",
		HealthURL: srv.URL,
	})
	if m == nil {
		t.Fatal("expected non-nil manager")
	}
	if m.Healthy() {
		t.Fatal("expected unhealthy before start")
	}
}

func TestManagerDefaultsApplied(t *testing.T) {
	m := process.New(process.Config{})
	if m == nil {
		t.Fatal("expected non-nil manager")
	}
	if m.Healthy() {
		t.Fatal("expected unhealthy before start")
	}
}

// TestManagerFailsFastOnPrematureExit verifies that when the subprocess exits
// before becoming healthy, Start returns promptly with the exit cause rather
// than polling health for the full StartTimeout.
func TestManagerFailsFastOnPrematureExit(t *testing.T) {
	// A health server that never reports healthy, so only a process exit can
	// end the startup loop.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	m := process.New(process.Config{
		// `false` exits immediately with a non-zero status.
		Binary:       "false",
		HealthURL:    srv.URL,
		StartTimeout: 10 * time.Second, // long, to prove we do NOT wait it out
	})

	start := time.Now()
	err := m.Start(context.Background())
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("expected Start to fail when the process exits prematurely")
	}
	if !strings.Contains(err.Error(), "exited during startup") {
		t.Fatalf("expected premature-exit error, got: %v", err)
	}
	if elapsed > 3*time.Second {
		t.Fatalf("Start should fail fast on premature exit; took %s", elapsed)
	}
}

func TestManagerStopBeforeStart(t *testing.T) {
	m := process.New(process.Config{Binary: "orb"})
	// Stop before Start must not panic (stopOnce guards the channel close)
	if err := m.Stop(); err != nil {
		t.Fatalf("unexpected error stopping unstarted manager: %v", err)
	}
	// Calling Stop a second time must also not panic
	if err := m.Stop(); err != nil {
		t.Fatalf("unexpected error on second Stop: %v", err)
	}
}
