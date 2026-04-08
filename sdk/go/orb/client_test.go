package orb_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/awslabs/open-resource-broker/sdk/go/orb"
)

func TestListTemplatesEmpty(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/templates/" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"templates": []any{}})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	templates, err := c.ListTemplates(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(templates) != 0 {
		t.Fatalf("expected 0 templates, got %d", len(templates))
	}
}

func TestGetTemplateNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]any{
			"success": false,
			"error": map[string]any{
				"code":    "TEMPLATE_NOT_FOUND",
				"message": "template not found",
				"details": map[string]any{},
			},
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	_, err = c.GetTemplate(context.Background(), "missing")
	if err == nil {
		t.Fatal("expected error")
	}
	if !errors.Is(err, orb.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got: %v", err)
	}
	var apiErr *orb.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("expected *APIError, got: %T", err)
	}
	if apiErr.Code != "TEMPLATE_NOT_FOUND" {
		t.Fatalf("expected code TEMPLATE_NOT_FOUND, got: %s", apiErr.Code)
	}
}

func TestRequestMachinesReturns202(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]string{
			"request_id": "req-abc123",
			"message":    "accepted",
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	req, err := c.RequestMachines(context.Background(), orb.RequestMachinesRequest{
		TemplateID: "tmpl-1",
		Count:      2,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if req.RequestID != "req-abc123" {
		t.Fatalf("expected req-abc123, got %s", req.RequestID)
	}
}

func TestBearerTokenSentPerRequest(t *testing.T) {
	callCount := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth := r.Header.Get("Authorization")
		if auth != "Bearer test-token" {
			t.Errorf("expected Bearer test-token, got: %s", auth)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"templates": []any{}})
	}))
	defer srv.Close()

	tokenCallCount := 0
	c, err := orb.NewClient(
		orb.WithBaseURL(srv.URL),
		orb.WithAuth(orb.WithBearerTokenFunc(func(ctx context.Context) (string, error) {
			tokenCallCount++
			return "test-token", nil
		})),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	for i := 0; i < 3; i++ {
		_, err := c.ListTemplates(context.Background())
		if err != nil {
			t.Fatalf("call %d: %v", i, err)
		}
		callCount++
	}

	if tokenCallCount != 3 {
		t.Fatalf("expected token func called 3 times, got %d", tokenCallCount)
	}
	_ = callCount
}

func TestHealthyReturnsTrueWithoutManagedProcess(t *testing.T) {
	c, err := orb.NewClient(
		orb.WithBaseURL("http://localhost:19999"),
		orb.WithAuth(orb.WithNoAuth()),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	if !c.Healthy() {
		t.Fatal("expected Healthy() to return true when not in managed-process mode")
	}
}
