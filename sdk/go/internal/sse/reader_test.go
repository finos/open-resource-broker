package sse_test

import (
	"io"
	"strings"
	"testing"

	"github.com/finos/open-resource-broker/sdk/go/internal/sse"
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

// The request status stream announces its outcome with a named frame just
// before the closing sentinel. Those frames must not surface as status updates.
func TestNamedOutcomeFramesYieldNoRequestAndSentinelIsLast(t *testing.T) {
	for _, outcome := range []string{
		"event: complete\ndata: {\"status\": \"failed\"}\n\n",
		"event: error\ndata: {\"error\": \"internal\"}\n\n",
		"event: timeout\ndata: {\"status\": \"timeout\"}\n\n",
	} {
		input := "data: {\"requests\":[{\"status\":\"in_progress\"}]}\n\n" + outcome + "data: {}\n\n"
		r := sse.NewReader(body(input))

		requests := 0
		sentinels := 0
		for f := r.Next(); f != nil; f = r.Next() {
			if sentinels > 0 {
				t.Fatalf("frame after sentinel for %q", outcome)
			}
			if sse.IsSentinel(f) {
				sentinels++
				continue
			}
			if sse.FirstRequest(sse.ParsePayload(f)) != nil {
				requests++
			}
		}
		if requests != 1 {
			t.Fatalf("expected exactly 1 status update for %q, got %d", outcome, requests)
		}
		if sentinels != 1 {
			t.Fatalf("expected the stream to end with the sentinel for %q", outcome)
		}
	}
}

func TestFrameCarriesEventName(t *testing.T) {
	r := sse.NewReader(body("event: timeout\ndata: {\"status\": \"timeout\"}\n\ndata: {}\n\n"))
	f := r.Next()
	if f == nil || f.Event != "timeout" {
		t.Fatalf("expected event timeout, got %+v", f)
	}
	// The event name must not leak into the next frame.
	f = r.Next()
	if f == nil || f.Event != "" || !sse.IsSentinel(f) {
		t.Fatalf("expected plain sentinel frame, got %+v", f)
	}
}

func TestParsePayloadGoldenServerOutput(t *testing.T) {
	golden := `{"requests":[{"request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","status":"complete","template_id":"t1","requested_count":2,"created_at":"2026-10-31T01:00:00Z","machine_ids":[],"message":"done","metadata":{},"request_type":"acquire","desired_capacity":1,"successful_count":2,"failed_count":0,"error_details":{},"provider_data":{},"version":0,"resource_ids":[],"machines":[{"machine_id":"i-0abc123def4567890","name":"ip-10-0-1-12","result":"succeed","status":"running","private_ip_address":"10.0.1.12","public_ip_address":"54.12.34.56","instance_type":"t3.micro","price_type":"ondemand","cloud_host_id":"ip-10-0-1-12.ec2.internal","launch_time":1793411323,"request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","message":""},{"machine_id":"i-0def456abc7890123","name":"ip-10-0-1-13","result":"executing","status":"pending","request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","message":"","launch_time":null,"cloud_host_id":null}]}],"message":"Request status retrieved successfully","count":1}`
	p := sse.ParsePayload(&sse.Frame{Data: []byte(golden)})
	req := sse.FirstRequest(p)
	if req == nil || req.Status != "complete" || len(req.Machines) != 2 {
		t.Fatalf("golden payload not parsed: %+v", req)
	}
	m := req.Machines[0]
	if m.PrivateIP != "10.0.1.12" || m.PublicIP != "54.12.34.56" || string(m.LaunchTime) != "1793411323" {
		t.Fatalf("machine fields not parsed: %+v", m)
	}
}

func TestParsePayloadAcceptsStringLaunchTime(t *testing.T) {
	data := `{"requests":[{"status":"running","machines":[{"machine_id":"m","launch_time":"2026-01-02T03:04:05Z"}]}]}`
	req := sse.FirstRequest(sse.ParsePayload(&sse.Frame{Data: []byte(data)}))
	if req == nil || string(req.Machines[0].LaunchTime) != "2026-01-02T03:04:05Z" {
		t.Fatalf("string launch_time not parsed: %+v", req)
	}
}

// One undecodable machine must not drop the frame or its siblings.
func TestParsePayloadSkipsOnlyTheBadMachine(t *testing.T) {
	data := `{"requests":[{"status":"complete","machines":[{"machine_id":"ok","launch_time":1},{"machine_id":["bad"]}]},{"status":"complete","requested_count":"x"}]}`
	p := sse.ParsePayload(&sse.Frame{Data: []byte(data)})
	req := sse.FirstRequest(p)
	if req == nil || req.Status != "complete" {
		t.Fatalf("frame dropped: %+v", p)
	}
	if len(req.Machines) != 1 || req.Machines[0].MachineID != "ok" {
		t.Fatalf("expected only the decodable machine, got %+v", req.Machines)
	}
}
