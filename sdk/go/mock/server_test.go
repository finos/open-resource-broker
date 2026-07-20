package mock_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/mock"
	"github.com/finos/open-resource-broker/sdk/go/orb"
)

func TestMockServerListTemplates(t *testing.T) {
	srv := mock.NewServer()
	defer srv.Close()

	srv.SetTemplates([]orb.Template{
		{TemplateID: "tmpl-1", Name: "test-template"},
	})

	c, err := srv.Client()
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	templates, err := c.ListTemplates(context.Background())
	if err != nil {
		t.Fatalf("ListTemplates: %v", err)
	}
	if len(templates) != 1 {
		t.Fatalf("expected 1 template, got %d", len(templates))
	}
	if templates[0].TemplateID != "tmpl-1" {
		t.Fatalf("unexpected template ID: %s", templates[0].TemplateID)
	}
}

func TestMockServerGetTemplateNotFound(t *testing.T) {
	srv := mock.NewServer()
	defer srv.Close()

	c, err := srv.Client()
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	_, err = c.GetTemplate(context.Background(), "nonexistent")
	if err == nil {
		t.Fatal("expected error")
	}
	if !errors.Is(err, orb.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got: %v", err)
	}
}

func TestMockServerRequestMachines(t *testing.T) {
	srv := mock.NewServer()
	defer srv.Close()

	c, err := srv.Client()
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	req, err := c.RequestMachines(context.Background(), orb.RequestMachinesRequest{
		TemplateID: "tmpl-1",
		Count:      2,
	})
	if err != nil {
		t.Fatalf("RequestMachines: %v", err)
	}
	if req.RequestID == "" {
		t.Fatal("expected non-empty request ID")
	}
}

func TestMockServerSSEStream(t *testing.T) {
	srv := mock.NewServer()
	defer srv.Close()

	srv.SetRequestStatus("req-test", "pending")

	c, err := srv.Client()
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	go func() {
		time.Sleep(150 * time.Millisecond)
		srv.SetRequestStatus("req-test", "complete")
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	final, err := c.WaitForCompletion(ctx, "req-test")
	if err != nil {
		t.Fatalf("WaitForCompletion: %v", err)
	}
	_ = final
}

// TestMockServerSSEReconnectAfterDisconnect exercises mid-stream disconnect
// recovery: the server drops the connection after the first frame (clean EOF),
// the client reconnects, and once the status becomes terminal the stream ends
// cleanly. This wires the SimulateSSEDisconnect hook into an actual test.
func TestMockServerSSEReconnectAfterDisconnect(t *testing.T) {
	srv := mock.NewServer()
	defer srv.Close()

	srv.SetRequestStatus("req-drop", "pending")
	srv.SimulateSSEDisconnect("req-drop", 1) // drop after the first frame each connect

	c, err := srv.Client()
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	stream, err := c.StreamRequestStatus(context.Background(),
		"req-drop", orb.WithSSEInterval(10*time.Millisecond))
	if err != nil {
		t.Fatalf("StreamRequestStatus: %v", err)
	}
	defer stream.Close()

	// First event arrives, then the server drops; the client must reconnect
	// and deliver another event rather than surfacing an error.
	ev, ok := stream.Next()
	if !ok {
		t.Fatal("expected at least one event before disconnect")
	}
	if ev.Err != nil {
		t.Fatalf("clean mid-stream disconnect must not surface an error, got: %v", ev.Err)
	}

	// Flip to terminal; a subsequent (reconnected) frame will end the stream.
	srv.SetRequestStatus("req-drop", "complete")

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	done := make(chan struct{})
	go func() {
		for {
			if _, ok := stream.Next(); !ok {
				close(done)
				return
			}
		}
	}()
	select {
	case <-done:
	case <-ctx.Done():
		t.Fatal("stream did not terminate after reconnect + terminal status")
	}
	if err := stream.Err(); err != nil {
		t.Fatalf("unexpected stream error after clean reconnect: %v", err)
	}
}
