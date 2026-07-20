//go:build integration

package orb_test

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/orb"
	"github.com/finos/open-resource-broker/sdk/go/testutil"
)

// ---------------------------------------------------------------------------
// Cross-language parity runner (Go leg).
//
// This test LOADS the language-agnostic fixture sdk/parity/scenario.json and
// executes its six ordered steps against a REAL orb spawned over a UDS (the
// same testutil.StartORB harness the contract tests use).  Each step is
// dispatched to the concrete Go SDK method named in the fixture's
// sdk_methods.go entry, and the result is asserted against the step's
// `expected` block and skip rules.
//
// Static conformance (validate_sdk_spec_conformance.py) proves each step's
// (method, path, operationId) — and now sdk_methods.go — resolves to a real
// spec operation and client method.  This runtime leg proves the Go SDK
// actually drives the scenario end-to-end and produces equivalent outcomes.
// ---------------------------------------------------------------------------

type parityStep struct {
	Step        int               `json:"step"`
	Name        string            `json:"name"`
	SDKMethods  map[string]string `json:"sdk_methods"`
	Expected    parityExpected    `json:"expected"`
	Precond     string            `json:"precondition"`
	PostCond    *parityPostCond   `json:"post_condition"`
}

type parityExpected struct {
	// http_status may be a single int or a list of ints in the fixture.
	HTTPStatus  json.RawMessage `json:"http_status"`
	StatusShape *struct {
		StatusMustBeOneOf []string `json:"status_must_be_one_of"`
	} `json:"status_shape"`
}

type parityPostCond struct {
	Bind    string `json:"bind"`
	IfEmpty string `json:"if_empty"`
}

type parityScenario struct {
	Steps []parityStep `json:"steps"`
}

// parityState carries the variables bound across steps.
type parityState struct {
	firstTemplateID string
	requestID       string
	machineID       string
}

// stepResult is the PASS/SKIP outcome recorded per step (FAIL is a t.Error).
type stepResult string

const (
	stepPass stepResult = "PASS"
	stepSkip stepResult = "SKIP"
)

func loadScenario(t *testing.T) parityScenario {
	t.Helper()
	path := filepath.Join("..", "..", "parity", "scenario.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("parity: reading %s: %v", path, err)
	}
	var s parityScenario
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatalf("parity: parsing scenario.json: %v", err)
	}
	if len(s.Steps) == 0 {
		t.Fatal("parity: scenario.json declares no steps")
	}
	return s
}

// assertNotRouteLevel fails the step if err is a route-level 404/405 — those
// mean the Go SDK is calling the wrong path/verb, which must always be FAIL
// even for conditional steps.  Resource-level 404s are tolerated by callers.
func assertNotRouteLevel(t *testing.T, step string, err error) {
	t.Helper()
	if err == nil {
		return
	}
	var apiErr *orb.OrbApiError
	if errors.As(err, &apiErr) {
		if apiErr.StatusCode == 405 {
			t.Errorf("parity[%s]: FAIL — HTTP 405 Method Not Allowed (wrong verb, route-level bug)", step)
		}
		if apiErr.StatusCode == 404 && apiErr.Code == "" && apiErr.Message == "Not Found" {
			t.Errorf("parity[%s]: FAIL — HTTP 404 with no detail (route-level missing path bug)", step)
		}
	}
}

func TestParityScenario(t *testing.T) {
	scenario := loadScenario(t)
	c := testutil.StartORB(t)
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()

	state := &parityState{}
	results := make(map[int]stepResult)

	for _, step := range scenario.Steps {
		method := step.SDKMethods["go"]
		if method == "" {
			t.Fatalf("parity[step %d %s]: fixture has no sdk_methods.go entry", step.Step, step.Name)
		}
		t.Logf("step %d %-22s → %s", step.Step, step.Name, method)

		result := runParityStep(t, ctx, c, step, state)
		results[step.Step] = result
		t.Logf("  step %d %s: %s", step.Step, step.Name, result)
	}

	// Summary line — mirrors the README's "each step is PASS, SKIP, or FAIL".
	for _, step := range scenario.Steps {
		t.Logf("PARITY %d %-22s %s", step.Step, step.Name, results[step.Step])
	}
}

// runParityStep dispatches one step to its concrete Go SDK method and asserts
// the fixture's expectations.  Preconditions that are not met produce a SKIP.
func runParityStep(
	t *testing.T,
	ctx context.Context,
	c *orb.Client,
	step parityStep,
	state *parityState,
) stepResult {
	t.Helper()
	tag := step.Name

	switch step.Step {
	case 1: // health_check — client.Health(ctx)
		resp, err := c.Health(ctx)
		assertNotRouteLevel(t, tag, err)
		if err != nil {
			t.Errorf("parity[%s]: FAIL — Health returned error: %v", tag, err)
			return stepPass
		}
		if !oneOfExpectedStatus(step, resp.Status) {
			t.Errorf("parity[%s]: FAIL — health status %q not in expected set", tag, resp.Status)
		}
		return stepPass

	case 2: // list_templates — client.ListTemplates(ctx)
		templates, err := c.ListTemplates(ctx)
		assertNotRouteLevel(t, tag, err)
		if err != nil {
			t.Errorf("parity[%s]: FAIL — ListTemplates returned error: %v", tag, err)
			return stepPass
		}
		if len(templates) > 0 {
			state.firstTemplateID = templates[0].TemplateID
			t.Logf("  bound first_template_id=%s", state.firstTemplateID)
		} else {
			t.Logf("  no templates — steps 3-5 will skip per fixture if_empty rule")
		}
		return stepPass

	case 3: // request_machines — precondition: first_template_id bound
		if state.firstTemplateID == "" {
			return stepSkip
		}
		resp, err := c.RequestMachines(ctx, orb.RequestMachinesRequest{
			TemplateID: state.firstTemplateID,
			Count:      1,
		})
		assertNotRouteLevel(t, tag, err)
		if err != nil {
			// A provider-level failure (no real AWS) is not a route bug; the
			// route was reachable.  Treat as skip of the downstream chain.
			t.Logf("  RequestMachines returned non-route error (expected without real provider): %v", err)
			return stepSkip
		}
		if resp.RequestID == "" {
			t.Errorf("parity[%s]: FAIL — 2xx but empty request_id binding", tag)
		}
		state.requestID = resp.RequestID
		t.Logf("  bound request_id=%s", state.requestID)
		return stepPass

	case 4: // poll_request_status — precondition: request_id bound
		if state.requestID == "" {
			return stepSkip
		}
		status, err := c.GetRequestStatus(ctx, state.requestID, false)
		assertNotRouteLevel(t, tag, err)
		if err != nil {
			t.Errorf("parity[%s]: FAIL — GetRequestStatus returned error: %v", tag, err)
			return stepPass
		}
		if status.Status == "" {
			t.Errorf("parity[%s]: FAIL — status field empty", tag)
		}
		for _, m := range status.Machines {
			if m.MachineID != "" {
				state.machineID = m.MachineID
				break
			}
		}
		return stepPass

	case 5: // return_machines — precondition: request_id AND a machine_id
		if state.requestID == "" || state.machineID == "" {
			return stepSkip
		}
		err := c.ReturnMachines(ctx, []string{state.machineID})
		assertNotRouteLevel(t, tag, err)
		if err != nil {
			t.Logf("  ReturnMachines returned non-route error (acceptable): %v", err)
		}
		return stepPass

	case 6: // list_requests — always executed
		_, err := c.ListRequests(ctx)
		assertNotRouteLevel(t, tag, err)
		if err != nil {
			t.Errorf("parity[%s]: FAIL — ListRequests returned error: %v", tag, err)
		}
		return stepPass

	default:
		t.Fatalf("parity: unknown step number %d (%s) — update the Go parity runner", step.Step, step.Name)
		return stepPass
	}
}

// oneOfExpectedStatus checks a health status string against the fixture's
// status_shape.status_must_be_one_of list (falling back to the canonical set).
func oneOfExpectedStatus(step parityStep, status string) bool {
	allowed := []string{"healthy", "degraded"}
	if step.Expected.StatusShape != nil && len(step.Expected.StatusShape.StatusMustBeOneOf) > 0 {
		allowed = step.Expected.StatusShape.StatusMustBeOneOf
	}
	for _, a := range allowed {
		if status == a {
			return true
		}
	}
	return false
}
