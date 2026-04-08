package mock_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/awslabs/open-resource-broker/sdk/go/mock"
	"github.com/awslabs/open-resource-broker/sdk/go/orb"
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
