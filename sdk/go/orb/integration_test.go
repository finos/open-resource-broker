//go:build integration

package orb_test

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/orb"
	"github.com/finos/open-resource-broker/sdk/go/testutil"
)

// assertRouteReachable fails the test if err indicates the route itself is
// missing/wrong (404 Not Found / 405 Method Not Allowed at the routing layer).
// Any other error (e.g. a 500 from missing cloud credentials, or a validation
// 4xx) is acceptable: it proves the route exists and was dispatched. A nil
// error is always fine.
func assertRouteReachable(t *testing.T, op string, err error) {
	t.Helper()
	if err == nil {
		return
	}
	var apiErr *orb.OrbApiError
	if errors.As(err, &apiErr) {
		switch apiErr.StatusCode {
		case 404:
			// A 404 whose body is a domain "not found" (e.g. NOT_FOUND code) is
			// fine — the route ran and the resource simply does not exist. A
			// bare 404 with no error code is a routing miss.
			if apiErr.Code == "" && !strings.Contains(strings.ToLower(apiErr.Message), "not found") {
				t.Errorf("%s: route not found (404 with no error body) — endpoint likely wrong", op)
			}
		case 405:
			t.Errorf("%s: method not allowed (405) — wrong HTTP verb for this route", op)
		}
	}
}

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

// TestIntegrationReadOnlyRoutesReachable exercises the read-only operations
// against a live orb and asserts each route is reachable (no 404/405 routing
// miss). This broadens real-orb coverage well beyond ListTemplates, catching a
// wrong verb or stale path (e.g. GetRequest hitting a non-existent route)
// without requiring cloud credentials.
func TestIntegrationReadOnlyRoutesReachable(t *testing.T) {
	c := testutil.StartORB(t)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	_, err := c.Health(ctx)
	assertRouteReachable(t, "Health", err)

	_, err = c.Info(ctx)
	assertRouteReachable(t, "Info", err)

	_, err = c.Metrics(ctx)
	assertRouteReachable(t, "Metrics", err)

	_, err = c.GetDashboardSummary(ctx)
	assertRouteReachable(t, "GetDashboardSummary", err)

	_, err = c.GetTelemetryStatus(ctx)
	assertRouteReachable(t, "GetTelemetryStatus", err)

	_, err = c.GetMe(ctx)
	assertRouteReachable(t, "GetMe", err)

	_, err = c.ListTemplates(ctx)
	assertRouteReachable(t, "ListTemplates", err)

	_, err = c.ListMachines(ctx)
	assertRouteReachable(t, "ListMachines", err)

	_, err = c.ListRequests(ctx)
	assertRouteReachable(t, "ListRequests", err)

	_, err = c.ListReturnRequests(ctx)
	assertRouteReachable(t, "ListReturnRequests", err)

	// Single-read on a non-existent ID — proves the /status route is wired
	// (GetRequest must NOT hit a non-existent GET /requests/{id} route).
	_, err = c.GetRequest(ctx, "does-not-exist")
	assertRouteReachable(t, "GetRequest", err)

	_, err = c.GetRequestStatus(ctx, "does-not-exist", false)
	assertRouteReachable(t, "GetRequestStatus", err)

	_, err = c.GetRequestTimeline(ctx, "does-not-exist")
	assertRouteReachable(t, "GetRequestTimeline", err)

	_, err = c.ListProviders(ctx)
	assertRouteReachable(t, "ListProviders", err)

	_, err = c.GetAllProviderSchemas(ctx)
	assertRouteReachable(t, "GetAllProviderSchemas", err)

	_, err = c.GetProvidersHealth(ctx)
	assertRouteReachable(t, "GetProvidersHealth", err)

	_, err = c.GetFullConfig(ctx)
	assertRouteReachable(t, "GetFullConfig", err)

	_, err = c.GetConfigSources(ctx)
	assertRouteReachable(t, "GetConfigSources", err)
}

// TestIntegrationStreamEventsReachable connects the global event bus against a
// live orb and asserts the connect is not a routing miss. It reads at most one
// frame then closes, so it does not block on a quiet event bus.
func TestIntegrationStreamEventsReachable(t *testing.T) {
	c := testutil.StartORB(t)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	stream, err := c.StreamEvents(ctx)
	if err != nil {
		assertRouteReachable(t, "StreamEvents", err)
		return
	}
	defer stream.Close()

	// Read one frame (or the ctx timeout / stream close) to confirm the route
	// produced a stream rather than a 404/405.
	select {
	case ev, ok := <-func() <-chan orb.Event {
		ch := make(chan orb.Event, 1)
		go func() {
			if e, ok := stream.Next(); ok {
				ch <- e
			}
			close(ch)
		}()
		return ch
	}():
		if ok && ev.Err != nil {
			assertRouteReachable(t, "StreamEvents", ev.Err)
		}
	case <-time.After(3 * time.Second):
		// No event within the window is fine — the route is up and streaming.
	}
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
