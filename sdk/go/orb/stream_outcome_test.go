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

// goldenCompleteFrame is request status output of the server's default
// scheduler formatter (numeric launch_time, *_ip_address keys, a machine whose
// launch_time is still null), served as one data frame.
const goldenCompleteFrame = "data: {\"requests\":[{\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"status\":\"complete\",\"template_id\":\"t1\",\"requested_count\":2,\"created_at\":\"2026-10-31T01:00:00Z\",\"machine_ids\":[],\"message\":\"done\",\"metadata\":{},\"request_type\":\"acquire\",\"desired_capacity\":1,\"successful_count\":2,\"failed_count\":0,\"error_details\":{},\"provider_data\":{},\"version\":0,\"resource_ids\":[],\"machines\":[{\"machine_id\":\"i-0abc123def4567890\",\"name\":\"ip-10-0-1-12\",\"result\":\"succeed\",\"status\":\"running\",\"private_ip_address\":\"10.0.1.12\",\"public_ip_address\":\"54.12.34.56\",\"instance_type\":\"t3.micro\",\"price_type\":\"ondemand\",\"cloud_host_id\":\"ip-10-0-1-12.ec2.internal\",\"launch_time\":1793411323,\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"message\":\"\"},{\"machine_id\":\"i-0def456abc7890123\",\"name\":\"ip-10-0-1-13\",\"result\":\"executing\",\"status\":\"pending\",\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"message\":\"\",\"launch_time\":null,\"cloud_host_id\":null}]}],\"message\":\"Request status retrieved successfully\",\"count\":1}\n\n"

// goldenRunningFrame is the same payload while the request is still in progress.
const goldenRunningFrame = "data: {\"requests\":[{\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"status\":\"in_progress\",\"template_id\":\"t1\",\"requested_count\":2,\"created_at\":\"2026-10-31T01:00:00Z\",\"machine_ids\":[],\"message\":\"done\",\"metadata\":{},\"request_type\":\"acquire\",\"desired_capacity\":1,\"successful_count\":2,\"failed_count\":0,\"error_details\":{},\"provider_data\":{},\"version\":0,\"resource_ids\":[],\"machines\":[{\"machine_id\":\"i-0abc123def4567890\",\"name\":\"ip-10-0-1-12\",\"result\":\"succeed\",\"status\":\"running\",\"private_ip_address\":\"10.0.1.12\",\"public_ip_address\":\"54.12.34.56\",\"instance_type\":\"t3.micro\",\"price_type\":\"ondemand\",\"cloud_host_id\":\"ip-10-0-1-12.ec2.internal\",\"launch_time\":1793411323,\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"message\":\"\"},{\"machine_id\":\"i-0def456abc7890123\",\"name\":\"ip-10-0-1-13\",\"result\":\"executing\",\"status\":\"pending\",\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"message\":\"\",\"launch_time\":null,\"cloud_host_id\":null}]}],\"message\":\"Request status retrieved successfully\",\"count\":1}\n\n"

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

func TestWaitForCompletionParsesServerPayloadWithMachines(t *testing.T) {
	c, _ := serveStream(t, goldenCompleteFrame+"event: complete\ndata: {\"status\": \"complete\"}\n\ndata: {}\n\n")

	last, err := c.WaitForCompletion(context.Background(), "req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if last.Status != "complete" || last.SuccessfulCount != 2 || last.RequestedCount != 2 {
		t.Fatalf("unexpected final event: %+v", last)
	}
	if len(last.Machines) != 2 {
		t.Fatalf("expected 2 machines, got %d", len(last.Machines))
	}
	m := last.Machines[0]
	if m.PrivateIP != "10.0.1.12" || m.PublicIP != "54.12.34.56" || m.LaunchTime != "1793411323" {
		t.Fatalf("machine fields not parsed: %+v", m)
	}
	if last.Machines[1].LaunchTime != "" {
		t.Fatalf("null launch_time must be empty, got %q", last.Machines[1].LaunchTime)
	}
}

func TestWaitForCompletionTimeoutKeepsLastRealStatusWithMachines(t *testing.T) {
	c, _ := serveStream(t, goldenRunningFrame+"event: timeout\ndata: {\"status\": \"timeout\"}\n\ndata: {}\n\n")

	last, err := c.WaitForCompletion(context.Background(), "req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73")

	if !errors.Is(err, orb.ErrStreamTimeout) {
		t.Fatalf("expected ErrStreamTimeout, got %v", err)
	}
	if last.Status != "in_progress" || len(last.Machines) != 2 {
		t.Fatalf("expected last real status with machines, got %+v", last)
	}
}
