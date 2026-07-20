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

	for r.scanner.Scan() {
		line := r.scanner.Text()

		if line == "" {
			// Blank line = end of event
			if len(dataLines) == 0 {
				continue
			}
			data := bytes.Join(dataLines, []byte("\n"))
			dataLines = dataLines[:0]
			return &Frame{Data: data}
		}

		if strings.HasPrefix(line, "data:") {
			val := strings.TrimPrefix(line, "data:")
			val = strings.TrimPrefix(val, " ")
			dataLines = append(dataLines, []byte(val))
		}
		// Ignore event:, id:, retry: lines — ORB doesn't use them
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

// OrbMachine represents a single machine entry in an ORB SSE request.
type OrbMachine struct {
	MachineID  string `json:"machine_id"`
	Name       string `json:"name"`
	Status     string `json:"status"`
	Result     string `json:"result"`
	PrivateIP  string `json:"private_ip"`
	PublicIP   string `json:"public_ip"`
	LaunchTime string `json:"launch_time"`
	Message    string `json:"message"`
}

// ParsePayload parses an ORB SSE data frame into its structured fields.
// Returns nil if the frame is the sentinel or cannot be parsed.
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
