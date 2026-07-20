package orb

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/internal/transport"
)

// ============================================================================
// System / Observability
// ============================================================================

// HealthResponse is returned by Health.
type HealthResponse struct {
	Status string `json:"status"`
}

// Health checks the ORB server's health.
//
// A 503 response is a valid, expected result meaning the server is degraded or
// unhealthy — it carries a parsed health body, not an error. Health therefore
// returns the parsed body for both 200 and 503 (matching the Kotlin/.NET SDKs)
// so a health-poll loop sees the degraded status rather than an exception.
// Other non-2xx statuses (e.g. 401/500) are still surfaced as errors.
func (c *Client) Health(ctx context.Context) (*HealthResponse, error) {
	if err := c.checkHealth(); err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/health", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	// Do not retry-loop on a 503: a degraded health response must be observed
	// directly rather than retried away.
	transport.DisableRetry(req)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, mapError(err)
	}
	defer resp.Body.Close()

	// 503 is a valid degraded/unhealthy health response, not an error.
	if resp.StatusCode >= 400 && resp.StatusCode != http.StatusServiceUnavailable {
		return nil, parseAPIError(resp)
	}

	var out HealthResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}

// MetricsResponse holds raw Prometheus-style metric text.
type MetricsResponse struct {
	Body string
}

// Metrics returns the raw Prometheus metrics text from the /metrics endpoint.
// The response body is returned as a string.
func (c *Client) Metrics(ctx context.Context) (*MetricsResponse, error) {
	if err := c.checkHealth(); err != nil {
		return nil, err
	}
	req, err := newGetRequest(ctx, c.baseURL+"/metrics")
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "text/plain")
	var body string
	if err := c.doText(req, &body); err != nil {
		return nil, err
	}
	return &MetricsResponse{Body: body}, nil
}

// InfoResponse holds server info.
type InfoResponse struct {
	Version   string `json:"version"`
	BuildDate string `json:"build_date,omitempty"`
	GitCommit string `json:"git_commit,omitempty"`
}

// Info returns build/version information about the running ORB server.
func (c *Client) Info(ctx context.Context) (*InfoResponse, error) {
	var resp InfoResponse
	if err := c.get(ctx, "/info", &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// DashboardSummary holds aggregated system dashboard data.
type DashboardSummary = map[string]any

// GetDashboardSummary returns the ORB system dashboard summary.
func (c *Client) GetDashboardSummary(ctx context.Context) (DashboardSummary, error) {
	var resp DashboardSummary
	if err := c.get(ctx, "/api/v1/system/dashboard", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// TelemetryStatus holds observability/telemetry configuration status.
type TelemetryStatus = map[string]any

// GetTelemetryStatus returns the current observability telemetry status.
func (c *Client) GetTelemetryStatus(ctx context.Context) (TelemetryStatus, error) {
	var resp TelemetryStatus
	if err := c.get(ctx, "/api/v1/observability/telemetry", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// Me holds the current authenticated user/session info.
type Me = map[string]any

// GetMe returns information about the current authenticated session.
func (c *Client) GetMe(ctx context.Context) (Me, error) {
	var resp Me
	if err := c.get(ctx, "/api/v1/me/", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ============================================================================
// Templates (additional operations)
// ============================================================================

// TemplateMutationResult is the response from template create/update/validate.
type TemplateMutationResult struct {
	TemplateID       string   `json:"template_id"`
	Status           string   `json:"status"`
	ValidationErrors []string `json:"validation_errors,omitempty"`
}

// ValidateTemplate validates a template definition without persisting it.
// The raw template body is passed as a map.
func (c *Client) ValidateTemplate(ctx context.Context, body map[string]any) (*TemplateMutationResult, error) {
	var resp TemplateMutationResult
	if err := c.post(ctx, "/api/v1/templates/validate", body, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// RefreshTemplates triggers a provider-driven template refresh.
// Returns the updated template list.
func (c *Client) RefreshTemplates(ctx context.Context) ([]Template, error) {
	var resp struct {
		Templates []templateJSON `json:"templates"`
	}
	if err := c.post(ctx, "/api/v1/templates/refresh", nil, &resp); err != nil {
		return nil, err
	}
	out := make([]Template, len(resp.Templates))
	for i, t := range resp.Templates {
		out[i] = templateFromJSON(t)
	}
	return out, nil
}

// GenerateTemplatesRequest is the input for GenerateTemplates.
type GenerateTemplatesRequest struct {
	Provider         string         `json:"provider,omitempty"`
	AllProviders     bool           `json:"all_providers,omitempty"`
	ProviderAPI      string         `json:"provider_api,omitempty"`
	ProviderType     string         `json:"provider_type,omitempty"`
	ProviderSpecific map[string]any `json:"provider_specific,omitempty"`
	Force            bool           `json:"force,omitempty"`
}

// GenerateTemplates triggers template generation from provider metadata.
func (c *Client) GenerateTemplates(ctx context.Context, req GenerateTemplatesRequest) ([]Template, error) {
	var resp struct {
		Templates []templateJSON `json:"templates"`
	}
	body := map[string]any{
		"provider":          req.Provider,
		"all_providers":     req.AllProviders,
		"provider_api":      req.ProviderAPI,
		"provider_type":     req.ProviderType,
		"provider_specific": req.ProviderSpecific,
		"force":             req.Force,
	}
	if err := c.post(ctx, "/api/v1/templates/generate", body, &resp); err != nil {
		return nil, err
	}
	out := make([]Template, len(resp.Templates))
	for i, t := range resp.Templates {
		out[i] = templateFromJSON(t)
	}
	return out, nil
}

// ============================================================================
// Machines (additional operations)
// ============================================================================

// SyncMachineStatus triggers an in-place status refresh for a single machine.
// Returns updated machine info.
func (c *Client) SyncMachineStatus(ctx context.Context, id string) ([]Machine, error) {
	var resp struct {
		Machines []machineJSON `json:"machines"`
	}
	if err := c.get(ctx, "/api/v1/machines/"+url.PathEscape(id)+"/status", &resp); err != nil {
		return nil, err
	}
	out := make([]Machine, len(resp.Machines))
	for i, m := range resp.Machines {
		out[i] = machineFromJSON(m)
	}
	return out, nil
}

// PurgeMachine removes a machine record from the ORB database.
// Unlike ReturnMachines this does NOT release capacity — use for cleanup only.
func (c *Client) PurgeMachine(ctx context.Context, id string) error {
	return c.delete(ctx, "/api/v1/machines/"+url.PathEscape(id))
}

// MachineMetricsOption filters the GetMachineMetrics call.
type MachineMetricsOption func(url.Values)

// WithMetricsRange sets the time range for machine metrics (e.g. "1h", "24h").
func WithMetricsRange(r string) MachineMetricsOption {
	return func(q url.Values) { q.Set("range", r) }
}

// MachineMetrics holds raw machine metrics data.
type MachineMetrics = map[string]any

// GetMachineMetrics returns time-series metrics for a specific machine.
func (c *Client) GetMachineMetrics(ctx context.Context, id string, opts ...MachineMetricsOption) (MachineMetrics, error) {
	q := url.Values{}
	for _, o := range opts {
		o(q)
	}
	path := "/api/v1/machines/" + url.PathEscape(id) + "/metrics"
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	var resp MachineMetrics
	if err := c.get(ctx, path, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ============================================================================
// Requests (additional operations)
// ============================================================================

// RequestStatus holds request status information, potentially with machines.
type RequestStatus struct {
	RequestID       string        `json:"request_id"`
	Status          string        `json:"status"`
	Message         string        `json:"message"`
	RequestedCount  int           `json:"requested_count"`
	SuccessfulCount int           `json:"successful_count"`
	FailedCount     int           `json:"failed_count"`
	Machines        []MachineInfo `json:"machines,omitempty"`
	CreatedAt       time.Time     `json:"created_at"`
	UpdatedAt       time.Time     `json:"updated_at"`
}

// GetRequestStatus returns the status for a single request by ID.
// Unlike GetRequest, this endpoint is optimised for lightweight status polling.
func (c *Client) GetRequestStatus(ctx context.Context, id string, verbose bool) (*RequestStatus, error) {
	path := "/api/v1/requests/" + url.PathEscape(id) + "/status"
	if verbose {
		path += "?verbose=true"
	}
	var envelope struct {
		Requests []requestJSON `json:"requests"`
	}
	if err := c.get(ctx, path, &envelope); err != nil {
		return nil, err
	}
	if len(envelope.Requests) == 0 {
		return nil, &OrbApiError{OrbError: OrbError{Message: "request not found", sentinel: ErrNotFound}, StatusCode: 404}
	}
	r := requestFromJSON(envelope.Requests[0])
	status := &RequestStatus{
		RequestID:       r.RequestID,
		Status:          r.Status,
		Message:         r.Message,
		RequestedCount:  r.RequestedCount,
		SuccessfulCount: r.SuccessfulCount,
		FailedCount:     r.FailedCount,
		Machines:        r.Machines,
		CreatedAt:       r.CreatedAt,
		UpdatedAt:       r.UpdatedAt,
	}
	return status, nil
}

// BatchRequestStatusRequest is the input for BatchGetRequestStatus.
type BatchRequestStatusRequest struct {
	RequestIDs []string `json:"requestIds"`
	Verbose    bool     `json:"verbose,omitempty"`
}

// BatchGetRequestStatus returns status for multiple request IDs in one call.
func (c *Client) BatchGetRequestStatus(ctx context.Context, req BatchRequestStatusRequest) ([]Request, error) {
	body := map[string]any{
		"requestIds": req.RequestIDs,
		"verbose":    req.Verbose,
	}
	var resp struct {
		Requests []requestJSON `json:"requests"`
	}
	if err := c.post(ctx, "/api/v1/requests/status", body, &resp); err != nil {
		return nil, err
	}
	out := make([]Request, len(resp.Requests))
	for i, r := range resp.Requests {
		out[i] = requestFromJSON(r)
	}
	return out, nil
}

// ListReturnRequestsOption filters the ListReturnRequests call.
type ListReturnRequestsOption func(url.Values)

// WithReturnRequestLimit limits the number of return requests returned.
func WithReturnRequestLimit(n int) ListReturnRequestsOption {
	return func(q url.Values) { q.Set("limit", strconv.Itoa(n)) }
}

// WithReturnRequestOffset sets the pagination offset.
func WithReturnRequestOffset(n int) ListReturnRequestsOption {
	return func(q url.Values) { q.Set("offset", strconv.Itoa(n)) }
}

// WithReturnRequestCursor sets the cursor for cursor-based pagination.
func WithReturnRequestCursor(cursor string) ListReturnRequestsOption {
	return func(q url.Values) { q.Set("cursor", cursor) }
}

// WithReturnRequestQuery sets a search query filter.
func WithReturnRequestQuery(q string) ListReturnRequestsOption {
	return func(v url.Values) { v.Set("q", q) }
}

// WithReturnRequestSort sets the sort order (e.g. "created_at", "-created_at").
func WithReturnRequestSort(sort string) ListReturnRequestsOption {
	return func(v url.Values) { v.Set("sort", sort) }
}

// WithReturnRequestProviderName filters return requests by provider name.
func WithReturnRequestProviderName(name string) ListReturnRequestsOption {
	return func(v url.Values) { v.Set("provider_name", name) }
}

// WithReturnRequestProviderType filters return requests by provider type.
func WithReturnRequestProviderType(pt string) ListReturnRequestsOption {
	return func(v url.Values) { v.Set("provider_type", pt) }
}

// ListReturnRequests returns all machine return requests.
func (c *Client) ListReturnRequests(ctx context.Context, opts ...ListReturnRequestsOption) ([]Request, error) {
	q := url.Values{}
	for _, o := range opts {
		o(q)
	}
	path := "/api/v1/requests/return"
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	var resp struct {
		Requests []requestJSON `json:"requests"`
	}
	if err := c.get(ctx, path, &resp); err != nil {
		return nil, err
	}
	out := make([]Request, len(resp.Requests))
	for i, r := range resp.Requests {
		out[i] = requestFromJSON(r)
	}
	return out, nil
}

// PurgeRequest permanently removes a request record.
// Unlike CancelRequest, purge is irreversible and removes all history.
func (c *Client) PurgeRequest(ctx context.Context, id string) error {
	return c.post(ctx, "/api/v1/requests/"+url.PathEscape(id)+"/purge", nil, nil)
}

// RequestTimeline holds the ordered event history for a request.
type RequestTimeline = map[string]any

// GetRequestTimeline returns the timeline of events for a request.
func (c *Client) GetRequestTimeline(ctx context.Context, id string) (RequestTimeline, error) {
	var resp RequestTimeline
	if err := c.get(ctx, "/api/v1/requests/"+url.PathEscape(id)+"/timeline", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ============================================================================
// Providers
// ============================================================================

// ProviderInfo holds a single provider's details.
type ProviderInfo = map[string]any

// ListProviders returns all registered providers.
func (c *Client) ListProviders(ctx context.Context) ([]ProviderInfo, error) {
	// The providers endpoint returns {"providers": [...]} wrapped response.
	var resp struct {
		Providers []ProviderInfo `json:"providers"`
	}
	if err := c.get(ctx, "/api/v1/providers/", &resp); err != nil {
		return nil, err
	}
	return resp.Providers, nil
}

// ProviderSchema holds the JSON schema for a provider's configuration.
type ProviderSchema = map[string]any

// GetAllProviderSchemas returns JSON schemas for all providers.
func (c *Client) GetAllProviderSchemas(ctx context.Context) (ProviderSchema, error) {
	var resp ProviderSchema
	if err := c.get(ctx, "/api/v1/providers/schemas", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// GetProviderSchema returns the JSON schema for a specific provider.
func (c *Client) GetProviderSchema(ctx context.Context, name string) (ProviderSchema, error) {
	var resp ProviderSchema
	if err := c.get(ctx, "/api/v1/providers/"+url.PathEscape(name)+"/schema", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ProviderHealth holds health information for all providers.
type ProviderHealth = map[string]any

// GetProvidersHealth returns health status for all registered providers.
func (c *Client) GetProvidersHealth(ctx context.Context) (ProviderHealth, error) {
	var resp ProviderHealth
	if err := c.get(ctx, "/api/v1/providers/health", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ============================================================================
// Admin
// ============================================================================

// AdminResult is the generic response for admin operations.
type AdminResult = map[string]any

// WipeDatabaseRequest is the input for WipeDatabase.
type WipeDatabaseRequest struct {
	Confirm bool `json:"confirm"`
}

// WipeDatabase wipes the ORB database. Requires confirm=true.
// WARNING: This is irreversible. Use only in test/dev environments.
func (c *Client) WipeDatabase(ctx context.Context, req WipeDatabaseRequest) (AdminResult, error) {
	body := map[string]any{"confirm": req.Confirm}
	var resp AdminResult
	if err := c.post(ctx, "/api/v1/admin/database/wipe", body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// InitRequest is the input for InitOrb.
type InitRequest struct {
	Force             bool `json:"force,omitempty"`
	GenerateTemplates bool `json:"generate_templates,omitempty"`
	Confirm           bool `json:"confirm"`
}

// InitOrb initialises the ORB system (creates default config, generates templates, etc.).
func (c *Client) InitOrb(ctx context.Context, req InitRequest) (AdminResult, error) {
	body := map[string]any{
		"force":              req.Force,
		"generate_templates": req.GenerateTemplates,
		"confirm":            req.Confirm,
	}
	var resp AdminResult
	if err := c.post(ctx, "/api/v1/admin/init", body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// InitORB is a deprecated alias for InitOrb.
//
// Deprecated: use InitOrb; the casing was normalised for cross-SDK consistency.
func (c *Client) InitORB(ctx context.Context, req InitRequest) (AdminResult, error) {
	return c.InitOrb(ctx, req)
}

// CleanupDatabaseRequest is the input for CleanupDatabase.
type CleanupDatabaseRequest struct {
	Confirm         bool     `json:"confirm"`
	OlderThanDays   int      `json:"older_than_days,omitempty"`
	RequestStatuses []string `json:"request_statuses,omitempty"`
	IncludeMachines bool     `json:"include_machines,omitempty"`
}

// CleanupDatabase removes old/stale records from the ORB database.
func (c *Client) CleanupDatabase(ctx context.Context, req CleanupDatabaseRequest) (AdminResult, error) {
	body := map[string]any{
		"confirm":          req.Confirm,
		"older_than_days":  req.OlderThanDays,
		"request_statuses": req.RequestStatuses,
		"include_machines": req.IncludeMachines,
	}
	var resp AdminResult
	if err := c.post(ctx, "/api/v1/admin/database/cleanup", body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ReloadConfig signals the ORB server to reload its configuration from disk.
func (c *Client) ReloadConfig(ctx context.Context) (AdminResult, error) {
	var resp AdminResult
	if err := c.post(ctx, "/api/v1/admin/reload-config", nil, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ============================================================================
// Config
// ============================================================================

// ConfigGetOption filters the GetFullConfig call.
type ConfigGetOption func(url.Values)

// WithConfigSource filters the config response to a single source.
func WithConfigSource(source string) ConfigGetOption {
	return func(q url.Values) { q.Set("source", source) }
}

// FullConfig holds the full ORB configuration tree.
type FullConfig = map[string]any

// GetFullConfig returns the complete ORB configuration.
func (c *Client) GetFullConfig(ctx context.Context, opts ...ConfigGetOption) (FullConfig, error) {
	q := url.Values{}
	for _, o := range opts {
		o(q)
	}
	path := "/api/v1/config/"
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	var resp FullConfig
	if err := c.get(ctx, path, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ConfigSources holds information about active configuration sources.
type ConfigSources = map[string]any

// GetConfigSources returns the list of active configuration sources.
func (c *Client) GetConfigSources(ctx context.Context) (ConfigSources, error) {
	var resp ConfigSources
	if err := c.get(ctx, "/api/v1/config/sources", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// GetConfigValue returns the value for a single configuration key.
func (c *Client) GetConfigValue(ctx context.Context, key string) (any, error) {
	var resp any
	if err := c.get(ctx, "/api/v1/config/"+url.PathEscape(key), &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// SetConfigValue sets the value for a single configuration key.
func (c *Client) SetConfigValue(ctx context.Context, key string, value any) (any, error) {
	body := map[string]any{"value": value}
	var resp any
	if err := c.put(ctx, "/api/v1/config/"+url.PathEscape(key), body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// SaveConfig persists the current in-memory configuration to disk.
// path specifies the target file path; pass empty string to use the default.
func (c *Client) SaveConfig(ctx context.Context, path string) (any, error) {
	body := map[string]any{"path": path}
	var resp any
	if err := c.post(ctx, "/api/v1/config/save", body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ValidateConfig validates the current configuration without saving.
func (c *Client) ValidateConfig(ctx context.Context) (any, error) {
	var resp any
	if err := c.post(ctx, "/api/v1/config/validate", nil, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}
