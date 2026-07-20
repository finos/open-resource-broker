package orb_test

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/orb"
)

// TestHealthReturnsBodyOn503 verifies that a 503 health response is returned as
// a parsed body (degraded/unhealthy status) rather than raised as an error, and
// that it is not retry-looped.
func TestHealthReturnsBodyOn503(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		fmt.Fprint(w, `{"status":"unhealthy"}`)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	h, err := c.Health(context.Background())
	if err != nil {
		t.Fatalf("expected no error for 503 health, got: %v", err)
	}
	if h.Status != "unhealthy" {
		t.Fatalf("expected status 'unhealthy', got %q", h.Status)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("503 health must not be retried; expected 1 call, got %d", got)
	}
}

// TestHealthErrorsOnNon503 verifies that other error statuses still surface as
// errors.
func TestHealthErrorsOnNon503(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprint(w, `{"error":{"code":"UNAUTHORIZED","message":"no token"}}`)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	if _, err := c.Health(context.Background()); !errors.Is(err, orb.ErrUnauthorized) {
		t.Fatalf("expected ErrUnauthorized, got: %v", err)
	}
}

// TestStreamRequestStatus4xxTerminal verifies that a 4xx connect status on the
// SSE endpoint produces a terminal typed error and does NOT reconnect.
func TestStreamRequestStatus4xxTerminal(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		fmt.Fprint(w, `{"error":{"code":"REQUEST_NOT_FOUND","message":"nope"}}`)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	stream, err := c.StreamRequestStatus(context.Background(), "req-x")
	if err != nil {
		t.Fatalf("StreamRequestStatus: %v", err)
	}
	defer stream.Close()

	ev, ok := stream.Next()
	if !ok {
		t.Fatal("expected a terminal error event")
	}
	if ev.Err == nil {
		t.Fatal("expected ev.Err to be set for a 4xx connect")
	}
	if !errors.Is(ev.Err, orb.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got: %v", ev.Err)
	}
	// Stream must be closed (no reconnect).
	if _, ok := stream.Next(); ok {
		t.Fatal("expected stream to be closed after 4xx (no reconnect)")
	}
	if !errors.Is(stream.Err(), orb.ErrNotFound) {
		t.Fatalf("expected stream.Err() ErrNotFound, got: %v", stream.Err())
	}
	// Give any (erroneous) reconnect a moment; assert exactly one connect.
	time.Sleep(50 * time.Millisecond)
	if got := calls.Load(); got != 1 {
		t.Fatalf("4xx SSE must be terminal; expected 1 connect, got %d", got)
	}
}

// TestStreamEvents4xxTerminal verifies the global event bus treats 4xx as
// terminal too.
func TestStreamEvents4xxTerminal(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/events/" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		fmt.Fprint(w, `{"error":{"code":"FORBIDDEN","message":"denied"}}`)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	stream, err := c.StreamEvents(context.Background())
	if err != nil {
		t.Fatalf("StreamEvents: %v", err)
	}
	defer stream.Close()

	ev, ok := stream.Next()
	if !ok || ev.Err == nil {
		t.Fatal("expected a terminal error event")
	}
	if !errors.Is(ev.Err, orb.ErrForbidden) {
		t.Fatalf("expected ErrForbidden, got: %v", ev.Err)
	}
}

// TestStreamEventsDeliversFrames verifies the global event bus yields raw data
// frames and terminates on the sentinel.
func TestStreamEventsDeliversFrames(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		f, _ := w.(http.Flusher)
		fmt.Fprint(w, "data: {\"type\":\"request.created\",\"id\":\"e1\"}\n\n")
		f.Flush()
		fmt.Fprint(w, "data: {}\n\n") // sentinel
		f.Flush()
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	stream, err := c.StreamEvents(context.Background(), orb.WithEventsType("request.created"))
	if err != nil {
		t.Fatalf("StreamEvents: %v", err)
	}
	defer stream.Close()

	ev, ok := stream.Next()
	if !ok {
		t.Fatal("expected one data frame")
	}
	if ev.Err != nil {
		t.Fatalf("unexpected error: %v", ev.Err)
	}
	if want := `{"type":"request.created","id":"e1"}`; string(ev.Data) != want {
		t.Fatalf("unexpected frame data:\n got  %s\n want %s", ev.Data, want)
	}
	if _, ok := stream.Next(); ok {
		t.Fatal("expected stream to close on sentinel")
	}
	if err := stream.Err(); err != nil {
		t.Fatalf("unexpected stream error: %v", err)
	}
}

// TestGetRequestUsesRequestRoute verifies GetRequest targets the
// GET /api/v1/requests/{id} route (the getRequest operation) and decodes the
// {"requests": [...]} envelope.
func TestGetRequestUsesRequestRoute(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"requests":[{"request_id":"req-7","status":"completed"}]}`)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	req, err := c.GetRequest(context.Background(), "req-7")
	if err != nil {
		t.Fatalf("GetRequest: %v", err)
	}
	if gotPath != "/api/v1/requests/req-7" {
		t.Fatalf("expected /api/v1/requests/req-7 route, got %q", gotPath)
	}
	if req.RequestID != "req-7" || req.Status != "completed" {
		t.Fatalf("unexpected request: %+v", req)
	}
}

// TestCancelRequestReason verifies the cancellation reason is sent as a query
// param.
func TestCancelRequestReason(t *testing.T) {
	var gotReason string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodDelete {
			t.Errorf("expected DELETE, got %s", r.Method)
		}
		gotReason = r.URL.Query().Get("reason")
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	if err := c.CancelRequest(context.Background(), "req-9", orb.WithCancelReason("user aborted")); err != nil {
		t.Fatalf("CancelRequest: %v", err)
	}
	if gotReason != "user aborted" {
		t.Fatalf("expected reason to be sent, got %q", gotReason)
	}
}

// TestApiErrorCarriesRequestID verifies the server request ID is captured onto
// OrbApiError for support correlation.
func TestApiErrorCarriesRequestID(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Request-ID", "corr-123")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		fmt.Fprint(w, `{"error":{"code":"CONFLICT","message":"exists"}}`)
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	_, err = c.GetTemplate(context.Background(), "x")
	var apiErr *orb.OrbApiError
	if !errors.As(err, &apiErr) {
		t.Fatalf("expected *OrbApiError, got %T", err)
	}
	if apiErr.RequestID != "corr-123" {
		t.Fatalf("expected RequestID corr-123, got %q", apiErr.RequestID)
	}
	if !errors.Is(err, orb.ErrConflict) {
		t.Fatalf("expected ErrConflict, got %v", err)
	}
}
