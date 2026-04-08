package process_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/awslabs/open-resource-broker/sdk/go/internal/process"
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
