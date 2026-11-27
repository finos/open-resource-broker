package orb_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/orb"
)

func TestHealth(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	h, err := c.Health(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if h.Status != "ok" {
		t.Fatalf("expected status ok, got %q", h.Status)
	}
}

func TestInfo(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/info" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"version": "1.8.3"})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	info, err := c.Info(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if info.Version != "1.8.3" {
		t.Fatalf("expected version 1.8.3, got %q", info.Version)
	}
}

func TestMetrics(t *testing.T) {
	const metricsBody = "# HELP go_goroutines Number of goroutines\ngo_goroutines 5\n"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/metrics" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		w.Write([]byte(metricsBody))
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	m, err := c.Metrics(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if m.Body != metricsBody {
		t.Fatalf("unexpected body: %q", m.Body)
	}
}

func TestGetDashboardSummary(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/system/dashboard" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"machines_total": 42})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	summary, err := c.GetDashboardSummary(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if summary["machines_total"] == nil {
		t.Fatal("expected machines_total in summary")
	}
}

func TestGetRequestStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/requests/req-99/status" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"requests": []map[string]any{
				{"request_id": "req-99", "status": "completed"},
			},
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	status, err := c.GetRequestStatus(context.Background(), "req-99", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if status.RequestID != "req-99" {
		t.Fatalf("expected req-99, got %q", status.RequestID)
	}
	if status.Status != "completed" {
		t.Fatalf("expected completed, got %q", status.Status)
	}
}

func TestBatchGetRequestStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/requests/status" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"requests": []map[string]any{
				{"request_id": "req-1", "status": "pending"},
				{"request_id": "req-2", "status": "completed"},
			},
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	requests, err := c.BatchGetRequestStatus(context.Background(), orb.BatchRequestStatusRequest{
		RequestIDs: []string{"req-1", "req-2"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(requests) != 2 {
		t.Fatalf("expected 2 requests, got %d", len(requests))
	}
}

func TestListReturnRequests(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/requests/return" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"requests": []map[string]any{
				{"request_id": "ret-1", "status": "completed"},
			},
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	requests, err := c.ListReturnRequests(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(requests) != 1 {
		t.Fatalf("expected 1 return request, got %d", len(requests))
	}
	if requests[0].RequestID != "ret-1" {
		t.Fatalf("expected ret-1, got %q", requests[0].RequestID)
	}
}

func TestPurgeRequest(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/requests/req-del/purge" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	if err := c.PurgeRequest(context.Background(), "req-del"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestPurgeMachine(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/machines/m-dead" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodDelete {
			t.Errorf("expected DELETE, got %s", r.Method)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	if err := c.PurgeMachine(context.Background(), "m-dead"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestGetFullConfig(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/config/" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"server": map[string]any{"port": 8000}})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	cfg, err := c.GetFullConfig(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, ok := cfg["server"]; !ok {
		t.Fatal("expected 'server' key in config")
	}
}

func TestSetConfigValue(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/config/server.port" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPut {
			t.Errorf("expected PUT, got %s", r.Method)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"value": 9000})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	_, err = c.SetConfigValue(context.Background(), "server.port", 9000)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestReloadConfig(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/admin/reload-config" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"status": "reloaded"})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	result, err := c.ReloadConfig(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["status"] != "reloaded" {
		t.Fatalf("expected status=reloaded, got %v", result["status"])
	}
}

func TestListProviders(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/providers/" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"providers": []map[string]any{
				{"name": "aws", "type": "aws"},
			},
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	providers, err := c.ListProviders(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(providers) != 1 {
		t.Fatalf("expected 1 provider, got %d", len(providers))
	}
}

func TestGetProviderSchema(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/providers/aws/schema" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"type": "object"})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	schema, err := c.GetProviderSchema(context.Background(), "aws")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if schema["type"] != "object" {
		t.Fatalf("expected type=object in schema")
	}
}

func TestListRequestsTimeRange(t *testing.T) {
	var got url.Values
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.URL.Query()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"requests": []any{}})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	// A non-UTC zone: the bound must be sent as the equivalent UTC instant.
	zone := time.FixedZone("plus2", 2*60*60)
	start := time.Date(2026, 11, 1, 12, 0, 0, 500000000, zone)
	end := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	if _, err := c.ListRequests(context.Background(),
		orb.WithRequestStartTime(start), orb.WithRequestEndTime(end)); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if v := got.Get("start_time"); v != "2026-11-01T10:00:00.500000Z" {
		t.Fatalf("start_time = %q", v)
	}
	if v := got.Get("end_time"); v != "2026-11-02T00:00:00.000000Z" {
		t.Fatalf("end_time = %q", v)
	}

	if _, err := c.ListRequests(context.Background()); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Has("start_time") || got.Has("end_time") {
		t.Fatalf("range sent when unset: %v", got)
	}
}
