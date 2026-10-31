package orb_test

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/finos/open-resource-broker/sdk/go/orb"
)

const inProgressFrame = "data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"in_progress\"}]}\n\n"

// serveStream serves body as the request status stream and counts connects.
func serveStream(t *testing.T, body string) (*orb.Client, *atomic.Int32) {
	t.Helper()
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, body)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
	}))
	t.Cleanup(srv.Close)
	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	return c, &calls
}

func TestWaitForCompletionReportsServerTimeout(t *testing.T) {
	c, calls := serveStream(t, inProgressFrame+"event: timeout\ndata: {\"status\": \"timeout\"}\n\ndata: {}\n\n")

	last, err := c.WaitForCompletion(context.Background(), "r1")

	if !errors.Is(err, orb.ErrStreamTimeout) {
		t.Fatalf("expected ErrStreamTimeout, got %v", err)
	}
	if last.Err != nil || last.Status != "in_progress" {
		t.Fatalf("expected the last status event (in_progress), got %+v", last)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("timeout must not reconnect; connects=%d", got)
	}
}

func TestWaitForCompletionReportsServerError(t *testing.T) {
	c, calls := serveStream(t, inProgressFrame+"event: error\ndata: {\"error\": \"internal\"}\n\ndata: {}\n\n")

	_, err := c.WaitForCompletion(context.Background(), "r1")

	if !errors.Is(err, orb.ErrStreamFailed) {
		t.Fatalf("expected ErrStreamFailed, got %v", err)
	}
	if errors.Is(err, orb.ErrStreamTimeout) {
		t.Fatal("error and timeout must be distinct")
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("error must not reconnect; connects=%d", got)
	}
}

func TestWaitForCompletionCompleteFrameIsNotAnError(t *testing.T) {
	body := inProgressFrame +
		"data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"complete\"}]}\n\n" +
		"event: complete\ndata: {\"status\": \"complete\"}\n\ndata: {}\n\n"
	c, _ := serveStream(t, body)

	last, err := c.WaitForCompletion(context.Background(), "r1")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if last.Status != "complete" {
		t.Fatalf("expected final status complete, got %q", last.Status)
	}
}

// Servers that predate the named frames send plain data frames and a sentinel.
func TestWaitForCompletionOldServerUnchanged(t *testing.T) {
	body := inProgressFrame +
		"data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"failed\"}]}\n\ndata: {}\n\n"
	c, _ := serveStream(t, body)

	last, err := c.WaitForCompletion(context.Background(), "r1")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if last.Status != "failed" {
		t.Fatalf("expected status failed, got %q", last.Status)
	}
}
