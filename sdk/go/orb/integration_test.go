//go:build integration

package orb_test

import (
	"context"
	"testing"
	"time"

	"github.com/awslabs/open-resource-broker/sdk/go/orb"
	"github.com/awslabs/open-resource-broker/sdk/go/testutil"
)

func TestIntegrationListTemplates(t *testing.T) {
	c := testutil.StartORB(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	templates, err := c.ListTemplates(ctx)
	if err != nil {
		t.Fatalf("ListTemplates: %v", err)
	}
	t.Logf("found %d templates", len(templates))
}

func TestIntegrationRequestAndStream(t *testing.T) {
	c := testutil.StartORB(t)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	templates, err := c.ListTemplates(ctx)
	if err != nil {
		t.Fatalf("ListTemplates: %v", err)
	}
	if len(templates) == 0 {
		t.Skip("no templates configured — skipping machine request test")
	}

	req, err := c.RequestMachines(ctx, orb.RequestMachinesRequest{
		TemplateID: templates[0].TemplateID,
		Count:      1,
	})
	if err != nil {
		// 500 is expected when no real AWS credentials are configured
		t.Skipf("RequestMachines returned error (expected without real AWS): %v", err)
	}
	t.Logf("request ID: %s", req.RequestID)

	final, err := c.WaitForCompletion(ctx, req.RequestID)
	if err != nil {
		t.Fatalf("WaitForCompletion: %v", err)
	}
	t.Logf("final status: %s", final.Status)
}
