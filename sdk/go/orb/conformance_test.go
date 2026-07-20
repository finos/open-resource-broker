package orb_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/finos/open-resource-broker/sdk/go/orb"
)

// specOperations parses sdk/spec/openapi.json and returns the set of
// (METHOD, path-template) operations it declares.
func specOperations(t *testing.T) map[string]bool {
	t.Helper()
	// Test runs from sdk/go/orb; the spec lives at sdk/spec/openapi.json.
	path := filepath.Join("..", "..", "spec", "openapi.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading spec: %v", err)
	}
	var doc struct {
		Paths map[string]map[string]json.RawMessage `json:"paths"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parsing spec: %v", err)
	}
	ops := make(map[string]bool)
	for p, methods := range doc.Paths {
		for m := range methods {
			switch strings.ToUpper(m) {
			case http.MethodGet, http.MethodPost, http.MethodPut,
				http.MethodDelete, http.MethodPatch, http.MethodHead:
				ops[strings.ToUpper(m)+" "+p] = true
			}
		}
	}
	return ops
}

// matchSpecPath finds the spec (method, template) that matches a concrete
// (method, path), treating {param} spec segments as wildcards. When several
// templates match (e.g. /config/sources vs /config/{key}), the one with the
// most literal (non-wildcard) segments wins, so an exact route is preferred
// over a parameterised one. Returns the matched "METHOD template" key, or "".
func matchSpecPath(ops map[string]bool, method, concrete string) string {
	cs := strings.Split(strings.Trim(concrete, "/"), "/")
	best := ""
	bestLiterals := -1
	for key := range ops {
		parts := strings.SplitN(key, " ", 2)
		if parts[0] != method {
			continue
		}
		ss := strings.Split(strings.Trim(parts[1], "/"), "/")
		if len(ss) != len(cs) {
			continue
		}
		ok := true
		literals := 0
		for i := range ss {
			if strings.HasPrefix(ss[i], "{") && strings.HasSuffix(ss[i], "}") {
				continue // wildcard
			}
			if ss[i] != cs[i] {
				ok = false
				break
			}
			literals++
		}
		if ok && literals > bestLiterals {
			best = key
			bestLiterals = literals
		}
	}
	return best
}

// recorder captures every (method, path) the client issues.
type recorder struct {
	mu   sync.Mutex
	hits map[string]bool
}

func (r *recorder) record(method, path string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.hits[method+" "+path] = true
}

// TestSpecConformance drives every client operation against a recording server
// and asserts that (a) each issued request matches a spec operation (no wrong
// verb / stale path), and (b) all 45 spec operations are exercised (no silent
// under-coverage). This runs without a live orb, closing the static-conformance
// gap independently of the integration leg.
func TestSpecConformance(t *testing.T) {
	ops := specOperations(t)
	if len(ops) != 45 {
		t.Fatalf("expected 45 spec operations, got %d (spec changed — update coverage)", len(ops))
	}

	rec := &recorder{hits: make(map[string]bool)}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec.record(r.Method, r.URL.Path)

		key := matchSpecPath(ops, r.Method, r.URL.Path)
		if key == "" {
			// Fail loudly: the client issued a request with no matching spec op.
			t.Errorf("client issued %s %s which matches NO spec operation", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		// SSE endpoints: emit the terminal sentinel and return.
		if strings.HasSuffix(r.URL.Path, "/stream") || r.URL.Path == "/api/v1/events/" {
			w.Header().Set("Content-Type", "text/event-stream")
			if f, ok := w.(http.Flusher); ok {
				w.Write([]byte("data: {}\n\n"))
				f.Flush()
			}
			return
		}
		if r.URL.Path == "/metrics" {
			w.Header().Set("Content-Type", "text/plain")
			w.Write([]byte("# metrics\n"))
			return
		}
		// Generic JSON body that satisfies the various decoders.
		w.Write([]byte(`{"templates":[],"machines":[],"requests":[],"providers":[],"status":"ok","value":null}`))
	}))
	defer srv.Close()

	c, err := orb.NewClient(orb.WithBaseURL(srv.URL), orb.WithAuth(orb.WithNoAuth()))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	ctx := context.Background()

	// Exercise every client operation. Errors are ignored: the recording server
	// is the assertion surface — we only care about the (method, path) issued.
	_, _ = c.Health(ctx)
	_, _ = c.Metrics(ctx)
	_, _ = c.Info(ctx)
	_, _ = c.GetDashboardSummary(ctx)
	_, _ = c.GetTelemetryStatus(ctx)
	_, _ = c.GetMe(ctx)

	_, _ = c.ListTemplates(ctx)
	_, _ = c.GetTemplate(ctx, "t1")
	_ = c.CreateTemplate(ctx, orb.CreateTemplateRequest{})
	_ = c.UpdateTemplate(ctx, "t1", orb.UpdateTemplateRequest{})
	_ = c.DeleteTemplate(ctx, "t1")
	_, _ = c.ValidateTemplate(ctx, map[string]any{})
	_, _ = c.RefreshTemplates(ctx)
	_, _ = c.GenerateTemplates(ctx, orb.GenerateTemplatesRequest{})

	_, _ = c.RequestMachines(ctx, orb.RequestMachinesRequest{})
	_ = c.ReturnMachines(ctx, []string{"m1"})
	_, _ = c.ListMachines(ctx)
	_, _ = c.GetMachine(ctx, "m1")
	_, _ = c.SyncMachineStatus(ctx, "m1")
	_ = c.PurgeMachine(ctx, "m1")
	_, _ = c.GetMachineMetrics(ctx, "m1")

	_, _ = c.GetRequest(ctx, "r1")
	_, _ = c.GetRequestStatus(ctx, "r1", false)
	_, _ = c.BatchGetRequestStatus(ctx, orb.BatchRequestStatusRequest{})
	_, _ = c.ListRequests(ctx)
	_, _ = c.ListReturnRequests(ctx)
	_ = c.CancelRequest(ctx, "r1")
	_ = c.PurgeRequest(ctx, "r1")
	_, _ = c.GetRequestTimeline(ctx, "r1")

	if stream, err := c.StreamRequestStatus(ctx, "r1"); err == nil {
		for {
			if _, ok := stream.Next(); !ok {
				break
			}
		}
		stream.Close()
	}
	if es, err := c.StreamEvents(ctx); err == nil {
		for {
			if _, ok := es.Next(); !ok {
				break
			}
		}
		es.Close()
	}

	_, _ = c.ListProviders(ctx)
	_, _ = c.GetAllProviderSchemas(ctx)
	_, _ = c.GetProviderSchema(ctx, "aws")
	_, _ = c.GetProvidersHealth(ctx)

	_, _ = c.WipeDatabase(ctx, orb.WipeDatabaseRequest{})
	_, _ = c.InitOrb(ctx, orb.InitRequest{})
	_, _ = c.CleanupDatabase(ctx, orb.CleanupDatabaseRequest{})
	_, _ = c.ReloadConfig(ctx)

	_, _ = c.GetFullConfig(ctx)
	_, _ = c.GetConfigSources(ctx)
	_, _ = c.GetConfigValue(ctx, "k")
	_, _ = c.SetConfigValue(ctx, "k", "v")
	_, _ = c.SaveConfig(ctx, "")
	_, _ = c.ValidateConfig(ctx)

	// Assert every spec operation was covered by exactly the matching route.
	covered := make(map[string]bool)
	rec.mu.Lock()
	for hit := range rec.hits {
		parts := strings.SplitN(hit, " ", 2)
		if key := matchSpecPath(ops, parts[0], parts[1]); key != "" {
			covered[key] = true
		}
	}
	rec.mu.Unlock()

	var missing []string
	for op := range ops {
		if !covered[op] {
			missing = append(missing, op)
		}
	}
	if len(missing) > 0 {
		t.Fatalf("the Go SDK does not exercise %d spec operation(s): %v", len(missing), missing)
	}
}
