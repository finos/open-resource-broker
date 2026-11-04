// Layer 5: SSE Reader + Reconnect
package sse

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"strings"
)

// Frame is a single parsed SSE event (the data field only — ORB only uses data:).
type Frame struct {
	Data []byte
	// Event is the SSE event: field, or "" when the frame has none. The request
	// status stream names its outcome frames "complete", "error" and "timeout";
	// servers that predate them send plain data frames only.
	Event string
}

// Reader parses SSE frames from an io.ReadCloser.
// ORB SSE wire format:
//
//	data: <json>\n\n        — normal event
//	data: {}\n\n            — terminal sentinel (stream done)
type Reader struct {
	scanner *bufio.Scanner
	body    io.ReadCloser
	err     error
}

// NewReader creates a new SSE Reader from an HTTP response body.
// The scanner buffer is pre-sized to 512 KiB (initial) with a 4 MiB maximum
// so that a single SSE data line up to 4 MiB does not trigger
// bufio.ErrTooLong (the default cap is only 64 KiB).
func NewReader(body io.ReadCloser) *Reader {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 0, 512*1024), 4*1024*1024)
	return &Reader{
		scanner: scanner,
		body:    body,
	}
}

// Next reads the next complete SSE frame. Returns nil when the stream ends.
// The caller should check Err() after Next() returns nil.
func (r *Reader) Next() *Frame {
	var dataLines [][]byte
	var event string

	for r.scanner.Scan() {
		line := r.scanner.Text()

		if line == "" {
			// Blank line = end of event
			if len(dataLines) == 0 {
				continue
			}
			data := bytes.Join(dataLines, []byte("\n"))
			dataLines = dataLines[:0]
			return &Frame{Data: data, Event: event}
		}

		if strings.HasPrefix(line, "event:") {
			event = strings.TrimPrefix(strings.TrimPrefix(line, "event:"), " ")
			continue
		}

		if strings.HasPrefix(line, "data:") {
			val := strings.TrimPrefix(line, "data:")
			val = strings.TrimPrefix(val, " ")
			dataLines = append(dataLines, []byte(val))
		}
		// Ignore id: and retry: lines — ORB doesn't use them
	}

	r.err = r.scanner.Err()
	return nil
}

// Err returns any scanner error (not io.EOF — that's normal termination).
func (r *Reader) Err() error {
	return r.err
}

// Close closes the underlying response body.
func (r *Reader) Close() {
	r.body.Close()
}

// IsSentinel reports whether a frame is the ORB terminal sentinel (data: {}).
func IsSentinel(f *Frame) bool {
	trimmed := bytes.TrimSpace(f.Data)
	return bytes.Equal(trimmed, []byte("{}"))
}

// TerminalStatuses is the set of ORB request statuses that indicate completion.
// Includes partial and timeout which are terminal in practice but missing from
// ORB's internal _TERMINAL_STATUSES set (ORB bug — SDK handles them anyway).
var TerminalStatuses = map[string]bool{
	"complete":  true,
	"completed": true,
	"failed":    true,
	"error":     true,
	"cancelled": true,
	"canceled":  true,
	"partial":   true,
	"timeout":   true,
}

// OrbSSEPayload is the JSON structure of ORB SSE data frames.
type OrbSSEPayload struct {
	Requests []OrbSSERequest `json:"requests"`
}

// OrbSSERequest represents a single request entry in an ORB SSE payload.
type OrbSSERequest struct {
	RequestID       string       `json:"request_id"`
	Status          string       `json:"status"`
	Message         string       `json:"message"`
	RequestedCount  int          `json:"requested_count"`
	SuccessfulCount int          `json:"successful_count"`
	FailedCount     int          `json:"failed_count"`
	Machines        []OrbMachine `json:"machines"`
}

// FlexString decodes a JSON string, number, boolean or null into a string.
// The server sends machine launch_time as a Unix timestamp number (null while
// unknown); a number is kept as its decimal text, e.g. "1793411323".
type FlexString string

// UnmarshalJSON implements json.Unmarshaler.
func (f *FlexString) UnmarshalJSON(data []byte) error {
	trimmed := bytes.TrimSpace(data)
	if bytes.Equal(trimmed, []byte("null")) {
		*f = ""
		return nil
	}
	if len(trimmed) > 0 && trimmed[0] == '"' {
		var s string
		if err := json.Unmarshal(trimmed, &s); err != nil {
			return err
		}
		*f = FlexString(s)
		return nil
	}
	*f = FlexString(trimmed)
	return nil
}

// OrbMachine represents a single machine entry in an ORB SSE request. The JSON
// keys match the server's machine reference serialisation.
type OrbMachine struct {
	MachineID  string     `json:"machine_id"`
	Name       string     `json:"name"`
	Status     string     `json:"status"`
	Result     string     `json:"result"`
	PrivateIP  string     `json:"private_ip_address"`
	PublicIP   string     `json:"public_ip_address"`
	LaunchTime FlexString `json:"launch_time"`
	Message    string     `json:"message"`
}

// UnmarshalJSON decodes a request entry element by element, so a machine entry
// that cannot be decoded is skipped instead of discarding the whole request.
func (r *OrbSSERequest) UnmarshalJSON(data []byte) error {
	type plain OrbSSERequest
	aux := struct {
		*plain
		Machines []json.RawMessage `json:"machines"`
	}{plain: (*plain)(r)}
	if err := json.Unmarshal(data, &aux); err != nil {
		return err
	}
	r.Machines = nil
	for _, raw := range aux.Machines {
		var m OrbMachine
		if err := json.Unmarshal(raw, &m); err == nil {
			r.Machines = append(r.Machines, m)
		}
	}
	return nil
}

// UnmarshalJSON decodes the payload request by request, so one malformed
// request entry does not discard its siblings.
func (p *OrbSSEPayload) UnmarshalJSON(data []byte) error {
	var aux struct {
		Requests []json.RawMessage `json:"requests"`
	}
	if err := json.Unmarshal(data, &aux); err != nil {
		return err
	}
	p.Requests = nil
	for _, raw := range aux.Requests {
		var r OrbSSERequest
		if err := json.Unmarshal(raw, &r); err == nil {
			p.Requests = append(p.Requests, r)
		}
	}
	return nil
}

// ParsePayload parses an ORB SSE data frame into its structured fields.
// Returns nil if the frame is the sentinel or is not a JSON object. Individual
// request and machine entries that cannot be decoded are skipped; they never
// cause the rest of the frame to be dropped.
func ParsePayload(f *Frame) *OrbSSEPayload {
	if IsSentinel(f) {
		return nil
	}
	var p OrbSSEPayload
	if err := json.Unmarshal(f.Data, &p); err != nil {
		return nil
	}
	return &p
}

// FirstRequest returns the first request entry from a parsed payload, or nil.
func FirstRequest(p *OrbSSEPayload) *OrbSSERequest {
	if p == nil || len(p.Requests) == 0 {
		return nil
	}
	return &p.Requests[0]
}
