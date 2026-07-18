package orb

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/internal/process"
	"github.com/finos/open-resource-broker/sdk/go/internal/sse"
	"github.com/finos/open-resource-broker/sdk/go/internal/transport"
)

// Client is the ORB API client.
type Client struct {
	baseURL    string
	httpClient *http.Client
	proc       *process.Manager
	scheduler  SchedulerType
}

// NewClient creates a new ORB client with the given options.
func NewClient(opts ...Option) (*Client, error) {
	cfg := defaultConfig()
	for _, o := range opts {
		o(&cfg)
	}

	var base http.RoundTripper
	if cfg.baseTransport != nil {
		base = cfg.baseTransport
	} else {
		base = http.DefaultTransport
	}

	// Auto-generate socket path if managed process has none
	if cfg.process != nil && cfg.process.SocketPath == "" && cfg.socketPath == "" {
		cfg.process.SocketPath = filepath.Join(os.TempDir(), fmt.Sprintf("orb-%d.sock", os.Getpid()))
	}

	// UDS transport: prefer explicit socketPath, fall back to managed process path
	socketPath := cfg.socketPath
	if socketPath == "" && cfg.process != nil {
		socketPath = cfg.process.SocketPath
	}
	if socketPath != "" {
		base = transport.UnixSocket(socketPath)
		cfg.baseURL = "http://localhost"
	}

	// Auth wraps base
	chain := cfg.auth.wrap(base)

	// Retry wraps auth
	chain = transport.NewRetryTransport(chain, cfg.maxRetries, 500*time.Millisecond)

	hc := &http.Client{
		Transport: chain,
		Timeout:   cfg.timeout,
	}

	c := &Client{
		baseURL:    cfg.baseURL,
		httpClient: hc,
		scheduler:  cfg.scheduler,
	}

	// Start managed process if configured
	if cfg.process != nil {
		if cfg.scheduler != SchedulerDefault {
			cfg.process.Args = append(cfg.process.Args, "--scheduler", string(cfg.scheduler))
		}
		pm := process.New(process.Config{
			Binary:       cfg.process.Binary,
			ConfigPath:   cfg.process.ConfigPath,
			Args:         cfg.process.Args,
			Env:          cfg.process.Env,
			SocketPath:   cfg.process.SocketPath,
			Port:         cfg.process.Port,
			StartTimeout: cfg.process.StartTimeout,
			StopTimeout:  cfg.process.StopTimeout,
		})
		ctx, cancel := context.WithTimeout(context.Background(), cfg.process.StartTimeout)
		defer cancel()
		if err := pm.Start(ctx); err != nil {
			return nil, fmt.Errorf("orb: starting managed process: %w", err)
		}
		c.proc = pm
	}

	return c, nil
}

// Close stops the managed process (if any) and releases resources.
func (c *Client) Close() error {
	if c.proc != nil {
		return c.proc.Stop()
	}
	return nil
}

// Healthy reports whether the client can reach ORB.
// Always returns true when not in managed-process mode.
func (c *Client) Healthy() bool {
	if c.proc != nil {
		return c.proc.Healthy()
	}
	return true
}

// checkHealth returns ErrORBUnavailable if the managed process is unhealthy.
func (c *Client) checkHealth() error {
	if c.proc != nil && !c.proc.Healthy() {
		return &OrbApiError{OrbError: OrbError{sentinel: ErrORBUnavailable, Message: "managed ORB process is unhealthy"}}
	}
	return nil
}

// --- HTTP helpers ---

func (c *Client) get(ctx context.Context, path string, out any) error {
	if err := c.checkHealth(); err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	return c.do(req, out)
}

func (c *Client) post(ctx context.Context, path string, body, out any) error {
	if err := c.checkHealth(); err != nil {
		return err
	}
	b, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	return c.do(req, out)
}

func (c *Client) put(ctx context.Context, path string, body, out any) error {
	if err := c.checkHealth(); err != nil {
		return err
	}
	b, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, c.baseURL+path, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	return c.do(req, out)
}

func (c *Client) delete(ctx context.Context, path string) error {
	if err := c.checkHealth(); err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, c.baseURL+path, nil)
	if err != nil {
		return err
	}
	return c.do(req, nil)
}

// newGetRequest builds an *http.Request for a GET to the given absolute URL.
// Used by methods that need custom Accept headers (e.g. text/plain for /metrics).
func newGetRequest(ctx context.Context, absURL string) (*http.Request, error) {
	return http.NewRequestWithContext(ctx, http.MethodGet, absURL, nil)
}

// doText executes req and decodes the response body as a plain string.
func (c *Client) doText(req *http.Request, out *string) error {
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return mapError(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return parseAPIError(resp)
	}
	if out != nil {
		b, err := io.ReadAll(resp.Body)
		if err != nil {
			return err
		}
		*out = string(b)
	}
	return nil
}

func (c *Client) do(req *http.Request, out any) error {
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return mapError(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return parseAPIError(resp)
	}

	if out != nil && resp.StatusCode != http.StatusNoContent {
		return json.NewDecoder(resp.Body).Decode(out)
	}
	return nil
}

// requestIDFromResponse extracts the server-assigned request ID from the
// response headers for support/correlation. ORB emits X-Request-ID; we also
// accept the common X-Request-Id / X-Correlation-ID spellings.
func requestIDFromResponse(resp *http.Response) string {
	for _, h := range []string{"X-Request-ID", "X-Request-Id", "X-Correlation-ID"} {
		if v := resp.Header.Get(h); v != "" {
			return v
		}
	}
	return ""
}

func parseAPIError(resp *http.Response) error {
	body, _ := io.ReadAll(resp.Body)
	requestID := requestIDFromResponse(resp)
	var orbErr struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
			Details any    `json:"details"`
		} `json:"error"`
	}
	if json.Unmarshal(body, &orbErr) == nil && orbErr.Error.Message != "" {
		return &OrbApiError{
			OrbError:   OrbError{Message: orbErr.Error.Message, sentinel: sentinelForStatus(resp.StatusCode)},
			StatusCode: resp.StatusCode,
			Code:       orbErr.Error.Code,
			RequestID:  requestID,
			Details:    orbErr.Error.Details,
		}
	}
	return &OrbApiError{
		OrbError:   OrbError{Message: http.StatusText(resp.StatusCode), sentinel: sentinelForStatus(resp.StatusCode)},
		StatusCode: resp.StatusCode,
		RequestID:  requestID,
	}
}

// --- Templates ---

type listTemplatesResponse struct {
	Templates []templateJSON `json:"templates"`
}

type templateJSON struct {
	TemplateID  string         `json:"template_id"`
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Provider    string         `json:"provider"`
	Config      map[string]any `json:"config"`
	CreatedAt   time.Time      `json:"created_at"`
	UpdatedAt   time.Time      `json:"updated_at"`
}

func templateFromJSON(t templateJSON) Template {
	return Template{
		TemplateID:  t.TemplateID,
		Name:        t.Name,
		Description: t.Description,
		Provider:    t.Provider,
		Config:      t.Config,
		CreatedAt:   t.CreatedAt,
		UpdatedAt:   t.UpdatedAt,
	}
}

// ListTemplates returns all templates.
func (c *Client) ListTemplates(ctx context.Context) ([]Template, error) {
	var resp listTemplatesResponse
	if err := c.get(ctx, "/api/v1/templates/", &resp); err != nil {
		return nil, err
	}
	out := make([]Template, len(resp.Templates))
	for i, t := range resp.Templates {
		out[i] = templateFromJSON(t)
	}
	return out, nil
}

// GetTemplate returns a single template by ID.
func (c *Client) GetTemplate(ctx context.Context, id string) (*Template, error) {
	var t templateJSON
	if err := c.get(ctx, "/api/v1/templates/"+url.PathEscape(id), &t); err != nil {
		return nil, err
	}
	tmpl := templateFromJSON(t)
	return &tmpl, nil
}

// CreateTemplate creates a new template.
func (c *Client) CreateTemplate(ctx context.Context, req CreateTemplateRequest) error {
	body := map[string]any{
		"name":        req.Name,
		"description": req.Description,
		"provider":    req.Provider,
		"config":      req.Config,
	}
	return c.post(ctx, "/api/v1/templates/", body, nil)
}

// UpdateTemplate updates an existing template.
func (c *Client) UpdateTemplate(ctx context.Context, id string, req UpdateTemplateRequest) error {
	body := map[string]any{
		"name":        req.Name,
		"description": req.Description,
		"config":      req.Config,
	}
	return c.put(ctx, "/api/v1/templates/"+url.PathEscape(id), body, nil)
}

// DeleteTemplate deletes a template by ID.
func (c *Client) DeleteTemplate(ctx context.Context, id string) error {
	return c.delete(ctx, "/api/v1/templates/"+url.PathEscape(id))
}

// --- Machines ---

type requestMachinesBody struct {
	TemplateID     string         `json:"templateId"`
	MachineCount   int            `json:"count"`
	AdditionalData map[string]any `json:"additionalData,omitempty"`
}

type requestMachinesResponse struct {
	RequestID      string `json:"request_id"`
	RequestIDCamel string `json:"requestId"`
	Message        string `json:"message"`
}

// requestMachinesResponseHF is the HostFactory camelCase variant.
type requestMachinesResponseHF struct {
	RequestID string `json:"requestId"`
	Message   string `json:"message"`
}

// machineJSONHF is the HostFactory camelCase machine payload.
// The server emits privateIpAddress / publicIpAddress (not privateIp / publicIp)
// in the HostFactory response format; tags must match the wire shape exactly.
type machineJSONHF struct {
	MachineID  string `json:"machineId"`
	VMType     string `json:"vmType"`
	PrivateIP  string `json:"privateIpAddress"`
	PublicIP   string `json:"publicIpAddress"`
	TemplateID string `json:"templateId"`
	RequestID  string `json:"requestId"`
}

func machineFromJSONHF(m machineJSONHF) Machine {
	return Machine{
		MachineID:  m.MachineID,
		PrivateIP:  m.PrivateIP,
		PublicIP:   m.PublicIP,
		TemplateID: m.TemplateID,
		RequestID:  m.RequestID,
	}
}

// requestJSONHF is the HostFactory camelCase request payload.
type requestJSONHF struct {
	RequestID    string          `json:"requestId"`
	ProviderName string          `json:"providerName"`
	ProviderType string          `json:"providerType"`
	ProviderAPI  string          `json:"providerApi"`
	Status       string          `json:"status"`
	Message      string          `json:"message"`
	Machines     []machineJSONHF `json:"machines"`
}

func requestFromJSONHF(r requestJSONHF) Request {
	machines := make([]MachineInfo, len(r.Machines))
	for i, m := range r.Machines {
		machines[i] = MachineInfo{
			MachineID: m.MachineID,
			PrivateIP: m.PrivateIP,
			PublicIP:  m.PublicIP,
		}
	}
	return Request{
		RequestID: r.RequestID,
		Status:    r.Status,
		Message:   r.Message,
		Machines:  machines,
	}
}

// RequestMachines submits a machine provisioning request.
// Returns a MachineRequest with the request ID for tracking.
func (c *Client) RequestMachines(ctx context.Context, req RequestMachinesRequest) (*MachineRequest, error) {
	body := requestMachinesBody{
		TemplateID:     req.TemplateID,
		MachineCount:   req.Count,
		AdditionalData: req.Metadata,
	}
	if c.scheduler == SchedulerHostFactory {
		var resp requestMachinesResponseHF
		if err := c.post(ctx, "/api/v1/machines/request", body, &resp); err != nil {
			return nil, err
		}
		return &MachineRequest{
			RequestID: resp.RequestID,
			Message:   resp.Message,
		}, nil
	}
	var resp requestMachinesResponse
	if err := c.post(ctx, "/api/v1/machines/request", body, &resp); err != nil {
		return nil, err
	}
	id := resp.RequestID
	if id == "" {
		id = resp.RequestIDCamel
	}
	return &MachineRequest{
		RequestID: id,
		Message:   resp.Message,
	}, nil
}

// ReturnMachines releases machines back to the pool.
func (c *Client) ReturnMachines(ctx context.Context, machineIDs []string) error {
	body := map[string]any{"machineIds": machineIDs}
	return c.post(ctx, "/api/v1/machines/return", body, nil)
}

type listMachinesResponse struct {
	Machines []machineJSON `json:"machines"`
}

type machineJSON struct {
	MachineID  string    `json:"machine_id"`
	Name       string    `json:"name"`
	Status     string    `json:"status"`
	PrivateIP  string    `json:"private_ip"`
	PublicIP   string    `json:"public_ip"`
	TemplateID string    `json:"template_id"`
	RequestID  string    `json:"request_id"`
	CreatedAt  time.Time `json:"created_at"`
}

func machineFromJSON(m machineJSON) Machine {
	return Machine{
		MachineID:  m.MachineID,
		Name:       m.Name,
		Status:     m.Status,
		PrivateIP:  m.PrivateIP,
		PublicIP:   m.PublicIP,
		TemplateID: m.TemplateID,
		RequestID:  m.RequestID,
		CreatedAt:  m.CreatedAt,
	}
}

// ListMachinesOption filters the ListMachines call.
type ListMachinesOption func(url.Values)

// WithMachineStatus filters machines by status.
func WithMachineStatus(status string) ListMachinesOption {
	return func(q url.Values) { q.Set("status", status) }
}

// WithMachineRequestID filters machines by request ID.
func WithMachineRequestID(id string) ListMachinesOption {
	return func(q url.Values) { q.Set("request_id", id) }
}

// WithMachineLimit limits the number of machines returned.
func WithMachineLimit(n int) ListMachinesOption {
	return func(q url.Values) { q.Set("limit", strconv.Itoa(n)) }
}

// ListMachines returns all machines, optionally filtered.
func (c *Client) ListMachines(ctx context.Context, opts ...ListMachinesOption) ([]Machine, error) {
	q := url.Values{}
	for _, o := range opts {
		o(q)
	}
	path := "/api/v1/machines/"
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	if c.scheduler == SchedulerHostFactory {
		var resp struct {
			Machines []machineJSONHF `json:"machines"`
		}
		if err := c.get(ctx, path, &resp); err != nil {
			return nil, err
		}
		out := make([]Machine, len(resp.Machines))
		for i, m := range resp.Machines {
			out[i] = machineFromJSONHF(m)
		}
		return out, nil
	}
	var resp listMachinesResponse
	if err := c.get(ctx, path, &resp); err != nil {
		return nil, err
	}
	out := make([]Machine, len(resp.Machines))
	for i, m := range resp.Machines {
		out[i] = machineFromJSON(m)
	}
	return out, nil
}

// GetMachine returns a single machine by ID.
func (c *Client) GetMachine(ctx context.Context, id string) (*Machine, error) {
	if c.scheduler == SchedulerHostFactory {
		var m machineJSONHF
		if err := c.get(ctx, "/api/v1/machines/"+url.PathEscape(id), &m); err != nil {
			return nil, err
		}
		machine := machineFromJSONHF(m)
		return &machine, nil
	}
	var m machineJSON
	if err := c.get(ctx, "/api/v1/machines/"+url.PathEscape(id), &m); err != nil {
		return nil, err
	}
	machine := machineFromJSON(m)
	return &machine, nil
}

// --- Requests ---

type requestJSON struct {
	RequestID       string        `json:"request_id"`
	Status          string        `json:"status"`
	Message         string        `json:"message"`
	RequestedCount  int           `json:"requested_count"`
	SuccessfulCount int           `json:"successful_count"`
	FailedCount     int           `json:"failed_count"`
	Machines        []machineJSON `json:"machines"`
	CreatedAt       time.Time     `json:"created_at"`
	UpdatedAt       time.Time     `json:"updated_at"`
}

func requestFromJSON(r requestJSON) Request {
	machines := make([]MachineInfo, len(r.Machines))
	for i, m := range r.Machines {
		machines[i] = MachineInfo{
			MachineID: m.MachineID,
			Name:      m.Name,
			Status:    m.Status,
			PrivateIP: m.PrivateIP,
			PublicIP:  m.PublicIP,
		}
	}
	return Request{
		RequestID:       r.RequestID,
		Status:          r.Status,
		Message:         r.Message,
		RequestedCount:  r.RequestedCount,
		SuccessfulCount: r.SuccessfulCount,
		FailedCount:     r.FailedCount,
		Machines:        machines,
		CreatedAt:       r.CreatedAt,
		UpdatedAt:       r.UpdatedAt,
	}
}

// GetRequest returns a single request by ID.
//
// There is no GET /api/v1/requests/{request_id} route in the ORB API — the
// single-read endpoint is GET /api/v1/requests/{request_id}/status, which
// returns the same {"requests": [...]} envelope as ListRequests. We decode
// into the envelope and extract the first element.
func (c *Client) GetRequest(ctx context.Context, id string) (*Request, error) {
	path := "/api/v1/requests/" + url.PathEscape(id) + "/status"
	if c.scheduler == SchedulerHostFactory {
		var envelope struct {
			Requests []requestJSONHF `json:"requests"`
		}
		if err := c.get(ctx, path, &envelope); err != nil {
			return nil, err
		}
		if len(envelope.Requests) == 0 {
			return nil, &OrbApiError{OrbError: OrbError{Message: "request not found", sentinel: ErrNotFound}, StatusCode: 404}
		}
		req := requestFromJSONHF(envelope.Requests[0])
		return &req, nil
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
	req := requestFromJSON(envelope.Requests[0])
	return &req, nil
}

// ListRequestsOption filters the ListRequests call.
type ListRequestsOption func(url.Values)

// WithRequestStatus filters requests by status.
func WithRequestStatus(status string) ListRequestsOption {
	return func(q url.Values) { q.Set("status", status) }
}

// WithRequestLimit limits the number of requests returned.
func WithRequestLimit(n int) ListRequestsOption {
	return func(q url.Values) { q.Set("limit", strconv.Itoa(n)) }
}

// WithRequestOffset sets the pagination offset.
func WithRequestOffset(n int) ListRequestsOption {
	return func(q url.Values) { q.Set("offset", strconv.Itoa(n)) }
}

// WithRequestCursor sets the cursor for cursor-based pagination.
func WithRequestCursor(cursor string) ListRequestsOption {
	return func(q url.Values) { q.Set("cursor", cursor) }
}

// WithRequestQuery sets a free-text search filter.
func WithRequestQuery(query string) ListRequestsOption {
	return func(q url.Values) { q.Set("q", query) }
}

// WithRequestSort sets the sort order (e.g. "created_at", "-created_at").
func WithRequestSort(sort string) ListRequestsOption {
	return func(q url.Values) { q.Set("sort", sort) }
}

// WithRequestProviderName filters requests by provider name.
func WithRequestProviderName(name string) ListRequestsOption {
	return func(q url.Values) { q.Set("provider_name", name) }
}

// WithRequestProviderType filters requests by provider type.
func WithRequestProviderType(pt string) ListRequestsOption {
	return func(q url.Values) { q.Set("provider_type", pt) }
}

// WithRequestTemplateID filters requests by template ID.
func WithRequestTemplateID(id string) ListRequestsOption {
	return func(q url.Values) { q.Set("template_id", id) }
}

// WithRequestType filters requests by request type.
func WithRequestType(rt string) ListRequestsOption {
	return func(q url.Values) { q.Set("request_type", rt) }
}

// ListRequests returns all requests, optionally filtered.
func (c *Client) ListRequests(ctx context.Context, opts ...ListRequestsOption) ([]Request, error) {
	q := url.Values{}
	for _, o := range opts {
		o(q)
	}
	path := "/api/v1/requests/"
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

// CancelRequestOption configures a CancelRequest call.
type CancelRequestOption func(url.Values)

// WithCancelReason sets the optional cancellation reason recorded server-side.
func WithCancelReason(reason string) CancelRequestOption {
	return func(q url.Values) { q.Set("reason", reason) }
}

// CancelRequest cancels a pending request. An optional reason may be supplied
// via WithCancelReason and is recorded on the server.
func (c *Client) CancelRequest(ctx context.Context, id string, opts ...CancelRequestOption) error {
	q := url.Values{}
	for _, o := range opts {
		o(q)
	}
	path := "/api/v1/requests/" + url.PathEscape(id)
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	return c.delete(ctx, path)
}

// --- Streaming ---

// StreamRequestStatus opens an SSE stream for the given request ID.
// Call stream.Next() in a loop; call stream.Close() when done.
//
// Deprecated: StreamRequest is a deprecated alias; use StreamRequestStatus.
func (c *Client) StreamRequest(ctx context.Context, id string, opts ...StreamOption) (*RequestStream, error) {
	return c.StreamRequestStatus(ctx, id, opts...)
}

// StreamRequestStatus opens an SSE stream for the given request ID.
// Call stream.Next() in a loop; call stream.Close() when done.
func (c *Client) StreamRequestStatus(ctx context.Context, id string, opts ...StreamOption) (*RequestStream, error) {
	if err := c.checkHealth(); err != nil {
		return nil, err
	}

	scfg := defaultStreamConfig()
	for _, o := range opts {
		o(&scfg)
	}

	streamCtx, cancel := context.WithCancel(ctx)
	ch := make(chan StreamEvent, 64)
	done := make(chan struct{})

	s := &RequestStream{
		ch:     ch,
		cancel: cancel,
		done:   done,
	}

	go func() {
		defer close(done)
		defer close(ch)
		c.runSSEProducer(streamCtx, id, scfg, ch, s)
	}()

	return s, nil
}

// sseClient returns an HTTP client for SSE streaming. It shares the main
// client's transport chain (auth, retry, UDS/TLS) but sets Timeout:0 so a
// long-lived stream is bounded only by the request context and the server-side
// stream timeout — not clipped at the 30s default client timeout.
func (c *Client) sseClient() *http.Client {
	return &http.Client{
		Transport: c.httpClient.Transport,
		Timeout:   0,
	}
}

func (c *Client) runSSEProducer(ctx context.Context, id string, scfg streamConfig, ch chan<- StreamEvent, s *RequestStream) {
	backoff := time.Second
	maxBackoff := 30 * time.Second

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		path := fmt.Sprintf("/api/v1/requests/%s/stream?interval=%.1f&timeout=%.1f",
			url.PathEscape(id),
			scfg.interval.Seconds(),
			scfg.timeout.Seconds(),
		)

		req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
		if err != nil {
			return
		}
		req.Header.Set("Accept", "text/event-stream")

		resp, err := c.sseClient().Do(req)
		if err != nil {
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
				backoff = min(backoff*2, maxBackoff)
				continue
			}
		}

		// Inspect the connect status BEFORE treating the body as SSE frames.
		// A 4xx means the request is fundamentally wrong (auth, not-found, bad
		// params) — the body is a JSON error, not an event stream, and there is
		// nothing to gain by reconnecting, so surface a typed terminal error.
		// A 5xx is a transient server-side failure: close the body and reconnect
		// with backoff.
		if resp.StatusCode >= 400 {
			if resp.StatusCode < 500 {
				err := parseAPIError(resp)
				resp.Body.Close()
				s.setErr(err)
				select {
				case ch <- StreamEvent{Err: err}:
				case <-ctx.Done():
				}
				return
			}
			resp.Body.Close()
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
				backoff = min(backoff*2, maxBackoff)
				continue
			}
		}

		terminal, gotEvents := c.consumeSSE(ctx, resp, ch, s)
		resp.Body.Close()

		if terminal {
			return
		}

		if gotEvents {
			backoff = time.Second // reset after successful connection
		}

		// Reconnect with backoff
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
			backoff = min(backoff*2, maxBackoff)
		}
	}
}

// consumeSSE reads SSE frames from resp and sends them to ch.
// Returns (terminal, gotEvents): terminal=true means no reconnect needed;
// gotEvents=true means at least one event was delivered (used to reset backoff).
func (c *Client) consumeSSE(ctx context.Context, resp *http.Response, ch chan<- StreamEvent, s *RequestStream) (bool, bool) {
	reader := sse.NewReader(resp.Body)
	defer reader.Close()

	var gotEvents bool

	for {
		select {
		case <-ctx.Done():
			return true, gotEvents
		default:
		}

		frame := reader.Next()
		if frame == nil {
			// Check for a scanner-level error (e.g. bufio.ErrTooLong on an
			// oversized frame). Surface it as a non-retryable stream error
			// rather than silently reconnecting, which would loop forever.
			if sErr := reader.Err(); sErr != nil {
				s.setErr(sErr)
				select {
				case ch <- StreamEvent{Err: sErr}:
				case <-ctx.Done():
				}
				return true, gotEvents
			}
			return false, gotEvents // clean EOF — reconnect
		}

		if sse.IsSentinel(frame) {
			return true, gotEvents // stream done normally
		}

		payload := sse.ParsePayload(frame)
		req := sse.FirstRequest(payload)
		if req == nil {
			continue
		}

		machines := make([]MachineInfo, len(req.Machines))
		for i, m := range req.Machines {
			machines[i] = MachineInfo{
				MachineID:  m.MachineID,
				Name:       m.Name,
				Status:     m.Status,
				Result:     m.Result,
				PrivateIP:  m.PrivateIP,
				PublicIP:   m.PublicIP,
				LaunchTime: m.LaunchTime,
				Message:    m.Message,
			}
		}

		event := StreamEvent{
			RequestID:       req.RequestID,
			Status:          req.Status,
			Message:         req.Message,
			RequestedCount:  req.RequestedCount,
			SuccessfulCount: req.SuccessfulCount,
			FailedCount:     req.FailedCount,
			Machines:        machines,
		}

		select {
		case ch <- event:
			gotEvents = true
		case <-ctx.Done():
			return true, gotEvents
		}

		if sse.TerminalStatuses[req.Status] {
			return true, gotEvents
		}
	}
}

// WaitForCompletion streams a request until it reaches a terminal status.
// Returns the final StreamEvent.
func (c *Client) WaitForCompletion(ctx context.Context, id string, opts ...StreamOption) (StreamEvent, error) {
	stream, err := c.StreamRequestStatus(ctx, id, opts...)
	if err != nil {
		return StreamEvent{}, err
	}
	defer stream.Close()

	var last StreamEvent
	for {
		event, ok := stream.Next()
		if !ok {
			break
		}
		last = event
	}
	return last, stream.Err()
}

// StreamEventsOption configures a StreamEvents call.
type StreamEventsOption func(url.Values)

// WithEventsSince replays events newer than the given ISO-8601 timestamp.
func WithEventsSince(ts string) StreamEventsOption {
	return func(q url.Values) { q.Set("since", ts) }
}

// WithEventsSinceSeq, combined with WithEventsSince, sets the sequence_id of the
// last event the client received, enabling gap detection on replay.
func WithEventsSinceSeq(seq int) StreamEventsOption {
	return func(q url.Values) { q.Set("since_seq", strconv.Itoa(seq)) }
}

// WithEventsType filters the global event stream to a single event type.
func WithEventsType(t string) StreamEventsOption {
	return func(q url.Values) { q.Set("type", t) }
}

// StreamEvents opens the global ORB event bus (GET /api/v1/events/) and streams
// raw SSE frames. Call stream.Next() in a loop; call stream.Close() when done.
//
// A 4xx connect status is terminal (surfaced as a typed error on the event and
// via stream.Err()); a 5xx triggers reconnect with backoff, matching the
// per-request status stream and the other SDKs.
func (c *Client) StreamEvents(ctx context.Context, opts ...StreamEventsOption) (*EventStream, error) {
	if err := c.checkHealth(); err != nil {
		return nil, err
	}

	q := url.Values{}
	for _, o := range opts {
		o(q)
	}
	path := "/api/v1/events/"
	if len(q) > 0 {
		path += "?" + q.Encode()
	}

	streamCtx, cancel := context.WithCancel(ctx)
	ch := make(chan Event, 64)
	done := make(chan struct{})

	s := &EventStream{ch: ch, cancel: cancel, done: done}

	go func() {
		defer close(done)
		defer close(ch)
		c.runEventProducer(streamCtx, path, ch, s)
	}()

	return s, nil
}

func (c *Client) runEventProducer(ctx context.Context, path string, ch chan<- Event, s *EventStream) {
	backoff := time.Second
	maxBackoff := 30 * time.Second

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
		if err != nil {
			return
		}
		req.Header.Set("Accept", "text/event-stream")

		resp, err := c.sseClient().Do(req)
		if err != nil {
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
				backoff = min(backoff*2, maxBackoff)
				continue
			}
		}

		if resp.StatusCode >= 400 {
			if resp.StatusCode < 500 {
				perr := parseAPIError(resp)
				resp.Body.Close()
				s.setErr(perr)
				select {
				case ch <- Event{Err: perr}:
				case <-ctx.Done():
				}
				return
			}
			resp.Body.Close()
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
				backoff = min(backoff*2, maxBackoff)
				continue
			}
		}

		terminal, gotEvents := c.consumeEvents(ctx, resp, ch, s)
		resp.Body.Close()

		if terminal {
			return
		}
		if gotEvents {
			backoff = time.Second
		}

		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
			backoff = min(backoff*2, maxBackoff)
		}
	}
}

func (c *Client) consumeEvents(ctx context.Context, resp *http.Response, ch chan<- Event, s *EventStream) (bool, bool) {
	reader := sse.NewReader(resp.Body)
	defer reader.Close()

	var gotEvents bool
	for {
		select {
		case <-ctx.Done():
			return true, gotEvents
		default:
		}

		frame := reader.Next()
		if frame == nil {
			if sErr := reader.Err(); sErr != nil {
				s.setErr(sErr)
				select {
				case ch <- Event{Err: sErr}:
				case <-ctx.Done():
				}
				return true, gotEvents
			}
			return false, gotEvents // clean EOF — reconnect
		}

		if sse.IsSentinel(frame) {
			return true, gotEvents
		}

		// Deliver a copy of the raw data: the reader reuses its buffer.
		data := make([]byte, len(frame.Data))
		copy(data, frame.Data)

		select {
		case ch <- Event{Data: data}:
			gotEvents = true
		case <-ctx.Done():
			return true, gotEvents
		}
	}
}
