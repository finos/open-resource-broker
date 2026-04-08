package sse_test

import (
	"io"
	"strings"
	"testing"

	"github.com/awslabs/open-resource-broker/sdk/go/internal/sse"
)

func body(s string) io.ReadCloser {
	return io.NopCloser(strings.NewReader(s))
}

func TestNextReturnsFrame(t *testing.T) {
	input := "data: {\"requests\":[{\"status\":\"in_progress\"}]}\n\n"
	r := sse.NewReader(body(input))
	f := r.Next()
	if f == nil {
		t.Fatal("expected frame, got nil")
	}
	if string(f.Data) != `{"requests":[{"status":"in_progress"}]}` {
		t.Fatalf("unexpected data: %s", f.Data)
	}
}

func TestSentinelDetected(t *testing.T) {
	input := "data: {}\n\n"
	r := sse.NewReader(body(input))
	f := r.Next()
	if f == nil {
		t.Fatal("expected frame")
	}
	if !sse.IsSentinel(f) {
		t.Fatal("expected sentinel")
	}
}

func TestMultipleFrames(t *testing.T) {
	input := "data: {\"requests\":[{\"status\":\"pending\"}]}\n\ndata: {\"requests\":[{\"status\":\"in_progress\"}]}\n\ndata: {}\n\n"
	r := sse.NewReader(body(input))

	f1 := r.Next()
	if f1 == nil || sse.IsSentinel(f1) {
		t.Fatal("expected non-sentinel frame 1")
	}
	f2 := r.Next()
	if f2 == nil || sse.IsSentinel(f2) {
		t.Fatal("expected non-sentinel frame 2")
	}
	f3 := r.Next()
	if f3 == nil || !sse.IsSentinel(f3) {
		t.Fatal("expected sentinel frame 3")
	}
}

func TestTerminalStatuses(t *testing.T) {
	for _, s := range []string{"complete", "completed", "failed", "error", "cancelled", "canceled", "partial", "timeout"} {
		if !sse.TerminalStatuses[s] {
			t.Errorf("expected %q to be terminal", s)
		}
	}
}

func TestParsePayload(t *testing.T) {
	input := `{"requests":[{"request_id":"req-1","status":"in_progress","requested_count":3,"successful_count":1,"failed_count":0,"machines":[]}]}`
	f := &sse.Frame{Data: []byte(input)}
	p := sse.ParsePayload(f)
	if p == nil {
		t.Fatal("expected parsed payload")
	}
	req := sse.FirstRequest(p)
	if req == nil {
		t.Fatal("expected first request")
	}
	if req.Status != "in_progress" {
		t.Fatalf("unexpected status: %s", req.Status)
	}
}
