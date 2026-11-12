package orb_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/finos/open-resource-broker/sdk/go/orb"
)

// formatServer answers every request with an empty template list. It records the
// X-ORB-Scheduler header it received and names answered in its own header unless empty.
func formatServer(t *testing.T, answered string, seen *string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*seen = r.Header.Get("X-ORB-Scheduler")
		w.Header().Set("Content-Type", "application/json")
		if answered != "" {
			w.Header().Set("X-ORB-Scheduler", answered)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"templates": []any{}})
	}))
}

func newFormatClient(t *testing.T, url string, opts ...orb.Option) *orb.Client {
	t.Helper()
	opts = append([]orb.Option{orb.WithBaseURL(url), orb.WithAuth(orb.WithNoAuth())}, opts...)
	c, err := orb.NewClient(opts...)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}

func TestEveryCallNamesTheDefaultFormat(t *testing.T) {
	var seen string
	srv := formatServer(t, "default", &seen)
	defer srv.Close()

	if _, err := newFormatClient(t, srv.URL).ListTemplates(context.Background()); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if seen != "default" {
		t.Fatalf("expected X-ORB-Scheduler default, got %q", seen)
	}
}

func TestResponseWithoutTheHeaderIsAccepted(t *testing.T) {
	var seen string
	srv := formatServer(t, "", &seen)
	defer srv.Close()

	c := newFormatClient(t, srv.URL, orb.WithScheduler(orb.SchedulerHostFactory))
	if _, err := c.ListTemplates(context.Background()); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if seen != "hostfactory" {
		t.Fatalf("expected X-ORB-Scheduler hostfactory, got %q", seen)
	}
}

func TestResponseNamingAnotherFormatIsAMismatch(t *testing.T) {
	var seen string
	srv := formatServer(t, "hostfactory", &seen)
	defer srv.Close()

	_, err := newFormatClient(t, srv.URL).ListTemplates(context.Background())

	var mismatch *orb.SchedulerMismatchError
	if !errors.As(err, &mismatch) {
		t.Fatalf("expected SchedulerMismatchError, got %v", err)
	}
	if mismatch.Expected != "default" || mismatch.Actual != "hostfactory" {
		t.Fatalf("unexpected mismatch %+v", mismatch)
	}
}

func TestResponseNamingTheRequestedFormatIsAccepted(t *testing.T) {
	var seen string
	srv := formatServer(t, "HostFactory", &seen)
	defer srv.Close()

	c := newFormatClient(t, srv.URL, orb.WithScheduler(orb.SchedulerHostFactory))
	if _, err := c.ListTemplates(context.Background()); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
