package orb_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/awslabs/open-resource-broker/sdk/go/orb"
)

// TestWithSchedulerAppendsFlag verifies that WithScheduler(SchedulerHostFactory)
// stores the scheduler type on the config (process arg appending is tested via
// integration; here we verify the option is accepted without error).
func TestWithSchedulerAppendsFlag(t *testing.T) {
	c, err := orb.NewClient(
		orb.WithBaseURL("http://localhost:19999"),
		orb.WithAuth(orb.WithNoAuth()),
		orb.WithScheduler(orb.SchedulerHostFactory),
	)
	if err != nil {
		t.Fatalf("NewClient with WithScheduler failed: %v", err)
	}
	defer c.Close()
}

// TestRequestMachinesHFSchedulerDecodesRequestId verifies that a client with
// SchedulerHostFactory decodes camelCase {"requestId": "req-123"}.
func TestRequestMachinesHFSchedulerDecodesRequestId(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]string{
			"requestId": "req-123",
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(
		orb.WithBaseURL(srv.URL),
		orb.WithAuth(orb.WithNoAuth()),
		orb.WithScheduler(orb.SchedulerHostFactory),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	req, err := c.RequestMachines(context.Background(), orb.RequestMachinesRequest{
		TemplateID: "tmpl-1",
		Count:      1,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if req.RequestID != "req-123" {
		t.Fatalf("expected req-123, got %q", req.RequestID)
	}
}

// TestRequestMachinesDefaultSchedulerDecodesRequestId verifies that the default
// client decodes snake_case {"request_id": "req-123"}.
func TestRequestMachinesDefaultSchedulerDecodesRequestId(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]string{
			"request_id": "req-123",
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(
		orb.WithBaseURL(srv.URL),
		orb.WithAuth(orb.WithNoAuth()),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	req, err := c.RequestMachines(context.Background(), orb.RequestMachinesRequest{
		TemplateID: "tmpl-1",
		Count:      1,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if req.RequestID != "req-123" {
		t.Fatalf("expected req-123, got %q", req.RequestID)
	}
}

// TestListMachinesHFSchedulerDecodesMachineId verifies that a HF client decodes
// camelCase machine fields: machineId, vmType, privateIp.
func TestListMachinesHFSchedulerDecodesMachineId(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"machines": []map[string]any{
				{
					"machineId": "i-123",
					"vmType":    "t3.medium",
					"privateIp": "10.0.0.1",
				},
			},
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(
		orb.WithBaseURL(srv.URL),
		orb.WithAuth(orb.WithNoAuth()),
		orb.WithScheduler(orb.SchedulerHostFactory),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	machines, err := c.ListMachines(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(machines) != 1 {
		t.Fatalf("expected 1 machine, got %d", len(machines))
	}
	if machines[0].MachineID != "i-123" {
		t.Fatalf("expected MachineID i-123, got %q", machines[0].MachineID)
	}
	if machines[0].PrivateIP != "10.0.0.1" {
		t.Fatalf("expected PrivateIP 10.0.0.1, got %q", machines[0].PrivateIP)
	}
}

// TestGetRequestHFSchedulerDecodesRequestId verifies that a HF client decodes
// camelCase request fields: requestId, providerName.
func TestGetRequestHFSchedulerDecodesRequestId(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"requestId":    "req-123",
			"providerName": "aws-default",
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(
		orb.WithBaseURL(srv.URL),
		orb.WithAuth(orb.WithNoAuth()),
		orb.WithScheduler(orb.SchedulerHostFactory),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	req, err := c.GetRequest(context.Background(), "req-123")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if req.RequestID != "req-123" {
		t.Fatalf("expected RequestID req-123, got %q", req.RequestID)
	}
}

// TestDefaultSchedulerUnchanged verifies the default scheduler still works with
// snake_case fields end-to-end.
func TestDefaultSchedulerUnchanged(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"machines": []map[string]any{
				{
					"machine_id": "m-456",
					"private_ip": "192.168.1.1",
				},
			},
		})
	}))
	defer srv.Close()

	c, err := orb.NewClient(
		orb.WithBaseURL(srv.URL),
		orb.WithAuth(orb.WithNoAuth()),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	machines, err := c.ListMachines(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(machines) != 1 {
		t.Fatalf("expected 1 machine, got %d", len(machines))
	}
	if machines[0].MachineID != "m-456" {
		t.Fatalf("expected MachineID m-456, got %q", machines[0].MachineID)
	}
	if machines[0].PrivateIP != "192.168.1.1" {
		t.Fatalf("expected PrivateIP 192.168.1.1, got %q", machines[0].PrivateIP)
	}
}
