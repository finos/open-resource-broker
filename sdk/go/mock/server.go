package mock

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"time"

	"github.com/awslabs/open-resource-broker/sdk/go/orb"
)

// Server is a fake ORB server for use in tests.
// It implements the ORB REST API and SSE streaming endpoint.
type Server struct {
	srv *httptest.Server
	mu  sync.RWMutex

	templates map[string]orb.Template
	requests  map[string]requestState
	machines  map[string]orb.Machine

	sseDisconnectAfter map[string]int
	sseDelay           map[string]time.Duration
}

type requestState struct {
	status   string
	message  string
	machines []orb.MachineInfo
}

// NewServer creates and starts a new fake ORB server.
func NewServer() *Server {
	s := &Server{
		templates:          make(map[string]orb.Template),
		requests:           make(map[string]requestState),
		machines:           make(map[string]orb.Machine),
		sseDisconnectAfter: make(map[string]int),
		sseDelay:           make(map[string]time.Duration),
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/api/v1/templates", s.handleTemplates)
	mux.HandleFunc("/api/v1/templates/", s.handleTemplate)
	mux.HandleFunc("/api/v1/machines/request/", s.handleRequestMachines)
	mux.HandleFunc("/api/v1/machines/return/", s.handleReturnMachines)
	mux.HandleFunc("/api/v1/machines", s.handleMachines)
	mux.HandleFunc("/api/v1/machines/", s.handleMachine)
	mux.HandleFunc("/api/v1/requests", s.handleRequests)
	mux.HandleFunc("/api/v1/requests/", s.handleRequest)
	s.srv = httptest.NewServer(mux)
	return s
}

// URL returns the base URL of the fake server.
func (s *Server) URL() string { return s.srv.URL }

// Client returns a pre-configured orb.Client pointing at this server.
func (s *Server) Client() (*orb.Client, error) {
	return orb.NewClient(
		orb.WithBaseURL(s.srv.URL),
		orb.WithAuth(orb.WithNoAuth()),
	)
}

// Close shuts down the fake server.
func (s *Server) Close() { s.srv.Close() }

// SetTemplates replaces all templates.
func (s *Server) SetTemplates(templates []orb.Template) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.templates = make(map[string]orb.Template, len(templates))
	for _, t := range templates {
		s.templates[t.TemplateID] = t
	}
}

// AddTemplate adds or replaces a single template.
func (s *Server) AddTemplate(t orb.Template) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.templates[t.TemplateID] = t
}

// SetRequestStatus sets the status for a request ID.
func (s *Server) SetRequestStatus(id, status string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	st := s.requests[id]
	st.status = status
	s.requests[id] = st
}

// SetRequestMachines sets the machines for a request ID.
func (s *Server) SetRequestMachines(id string, machines []orb.MachineInfo) {
	s.mu.Lock()
	defer s.mu.Unlock()
	st := s.requests[id]
	st.machines = machines
	s.requests[id] = st
}

// SimulateSSEDisconnect causes the SSE stream for id to disconnect after afterN events.
func (s *Server) SimulateSSEDisconnect(id string, afterN int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sseDisconnectAfter[id] = afterN
}

// SimulateSSEDelay adds a delay between SSE events for id.
func (s *Server) SimulateSSEDelay(id string, d time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sseDelay[id] = d
}

// --- handlers ---

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "healthy", "service": "open-resource-broker", "version": "test"})
}

func (s *Server) handleTemplates(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		s.mu.RLock()
		list := make([]map[string]any, 0, len(s.templates))
		for _, t := range s.templates {
			list = append(list, templateToJSON(t))
		}
		s.mu.RUnlock()
		writeJSON(w, http.StatusOK, map[string]any{"templates": list})
	case http.MethodPost:
		writeJSON(w, http.StatusCreated, map[string]string{"message": "created"})
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleTemplate(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/templates/")
	// Empty id means this is a collection request (e.g. GET /api/v1/templates/)
	if id == "" {
		s.handleTemplates(w, r)
		return
	}
	switch r.Method {
	case http.MethodGet:
		s.mu.RLock()
		t, ok := s.templates[id]
		s.mu.RUnlock()
		if !ok {
			writeError(w, http.StatusNotFound, "TEMPLATE_NOT_FOUND", "template not found")
			return
		}
		writeJSON(w, http.StatusOK, templateToJSON(t))
	case http.MethodPut:
		writeJSON(w, http.StatusOK, map[string]string{"message": "updated"})
	case http.MethodDelete:
		w.WriteHeader(http.StatusNoContent)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleRequestMachines(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	id := fmt.Sprintf("req-%d", time.Now().UnixNano())
	s.mu.Lock()
	s.requests[id] = requestState{status: "pending"}
	s.mu.Unlock()
	writeJSON(w, http.StatusAccepted, map[string]string{
		"request_id": id,
		"message":    "request accepted",
	})
}

func (s *Server) handleReturnMachines(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"message": "machines returned"})
}

func (s *Server) handleMachines(w http.ResponseWriter, r *http.Request) {
	s.mu.RLock()
	list := make([]map[string]any, 0, len(s.machines))
	for _, m := range s.machines {
		list = append(list, machineToJSON(m))
	}
	s.mu.RUnlock()
	writeJSON(w, http.StatusOK, map[string]any{"machines": list})
}

func (s *Server) handleMachine(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/machines/")
	s.mu.RLock()
	m, ok := s.machines[id]
	s.mu.RUnlock()
	if !ok {
		writeError(w, http.StatusNotFound, "MACHINE_NOT_FOUND", "machine not found")
		return
	}
	writeJSON(w, http.StatusOK, machineToJSON(m))
}

func (s *Server) handleRequests(w http.ResponseWriter, r *http.Request) {
	s.mu.RLock()
	list := make([]map[string]any, 0, len(s.requests))
	for id, st := range s.requests {
		list = append(list, map[string]any{
			"request_id": id,
			"status":     st.status,
			"message":    st.message,
			"machines":   st.machines,
		})
	}
	s.mu.RUnlock()
	writeJSON(w, http.StatusOK, map[string]any{"requests": list})
}

func (s *Server) handleRequest(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/requests/")
	if strings.HasSuffix(path, "/stream") {
		id := strings.TrimSuffix(path, "/stream")
		s.handleSSE(w, r, id)
		return
	}
	id := path
	switch r.Method {
	case http.MethodGet:
		s.mu.RLock()
		st, ok := s.requests[id]
		s.mu.RUnlock()
		if !ok {
			writeError(w, http.StatusNotFound, "REQUEST_NOT_FOUND", "request not found")
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"request_id": id,
			"status":     st.status,
			"message":    st.message,
			"machines":   st.machines,
		})
	case http.MethodDelete:
		w.WriteHeader(http.StatusNoContent)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleSSE(w http.ResponseWriter, r *http.Request, id string) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	s.mu.RLock()
	delay := s.sseDelay[id]
	disconnectAfter := s.sseDisconnectAfter[id]
	s.mu.RUnlock()

	terminalStatuses := map[string]bool{
		"complete": true, "completed": true, "failed": true,
		"error": true, "cancelled": true, "canceled": true,
		"partial": true, "timeout": true,
	}

	sent := 0
	for {
		select {
		case <-r.Context().Done():
			return
		default:
		}

		if disconnectAfter > 0 && sent >= disconnectAfter {
			return
		}

		s.mu.RLock()
		st := s.requests[id]
		s.mu.RUnlock()

		payload := map[string]any{
			"requests": []map[string]any{
				{
					"request_id":       id,
					"status":           st.status,
					"message":          st.message,
					"machines":         st.machines,
					"requested_count":  0,
					"successful_count": 0,
					"failed_count":     0,
				},
			},
		}
		data, _ := json.Marshal(payload)
		fmt.Fprintf(w, "data: %s\n\n", data)
		flusher.Flush()
		sent++

		if terminalStatuses[st.status] {
			fmt.Fprintf(w, "data: {}\n\n")
			flusher.Flush()
			return
		}

		if delay > 0 {
			time.Sleep(delay)
		} else {
			time.Sleep(100 * time.Millisecond)
		}
	}
}

// templateToJSON converts an orb.Template to a JSON-serialisable map using
// the field names the client expects.
func templateToJSON(t orb.Template) map[string]any {
	return map[string]any{
		"template_id": t.TemplateID,
		"name":        t.Name,
		"description": t.Description,
		"provider":    t.Provider,
		"config":      t.Config,
		"created_at":  t.CreatedAt,
		"updated_at":  t.UpdatedAt,
	}
}

// machineToJSON converts an orb.Machine to a JSON-serialisable map using
// the snake_case field names the client expects.
func machineToJSON(m orb.Machine) map[string]any {
	return map[string]any{
		"machine_id":  m.MachineID,
		"name":        m.Name,
		"status":      m.Status,
		"private_ip":  m.PrivateIP,
		"public_ip":   m.PublicIP,
		"template_id": m.TemplateID,
		"request_id":  m.RequestID,
		"created_at":  m.CreatedAt,
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{
		"success": false,
		"error": map[string]any{
			"code":    code,
			"message": message,
			"details": map[string]any{},
		},
	})
}
