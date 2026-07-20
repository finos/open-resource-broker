package orb

import (
	"context"
	"sync/atomic"
)

// StreamEvent is a single status update from the ORB SSE stream.
// Err is non-nil when the stream encountered a non-retryable transport error
// (e.g. an SSE frame larger than the reader buffer limit). When Err is set
// the event carries no payload fields and the stream will not reconnect.
type StreamEvent struct {
	RequestID       string
	Status          string
	Message         string
	RequestedCount  int
	SuccessfulCount int
	FailedCount     int
	Machines        []MachineInfo
	Err             error
}

// MachineInfo holds per-machine status within a StreamEvent.
type MachineInfo struct {
	MachineID  string
	Name       string
	Status     string
	Result     string
	PrivateIP  string
	PublicIP   string
	LaunchTime string
	Message    string
}

// RequestStream is a live SSE stream for a single ORB request.
// Call Next() in a loop; call Close() when done or on error.
type RequestStream struct {
	ch     chan StreamEvent
	cancel context.CancelFunc
	errPtr atomic.Pointer[error]
	done   chan struct{}
}

// Next returns the next event. ok is false when the stream is closed.
func (s *RequestStream) Next() (StreamEvent, bool) {
	ev, ok := <-s.ch
	return ev, ok
}

// Err returns any error that caused the stream to close abnormally.
func (s *RequestStream) Err() error {
	if p := s.errPtr.Load(); p != nil {
		return *p
	}
	return nil
}

// setErr records the first terminal error on the stream. Subsequent calls are
// no-ops so the earliest cause is preserved.
func (s *RequestStream) setErr(err error) {
	if err == nil {
		return
	}
	s.errPtr.CompareAndSwap(nil, &err)
}

// Close stops the stream and waits for the producer goroutine to exit.
// Safe to call multiple times.
func (s *RequestStream) Close() {
	s.cancel()
	for range s.ch {
	}
	<-s.done
}

// Event is a single frame from the global ORB event bus (GET /api/v1/events/).
// Data holds the raw JSON payload of the SSE data: field. Err is non-nil when
// the stream terminated on a non-retryable transport or HTTP error.
type Event struct {
	Data []byte
	Err  error
}

// EventStream is a live SSE stream over the global ORB event bus.
// Call Next() in a loop; call Close() when done.
type EventStream struct {
	ch     chan Event
	cancel context.CancelFunc
	errPtr atomic.Pointer[error]
	done   chan struct{}
}

// Next returns the next event. ok is false when the stream is closed.
func (s *EventStream) Next() (Event, bool) {
	ev, ok := <-s.ch
	return ev, ok
}

// Err returns any error that caused the stream to close abnormally.
func (s *EventStream) Err() error {
	if p := s.errPtr.Load(); p != nil {
		return *p
	}
	return nil
}

func (s *EventStream) setErr(err error) {
	if err == nil {
		return
	}
	s.errPtr.CompareAndSwap(nil, &err)
}

// Close stops the stream and waits for the producer goroutine to exit.
// Safe to call multiple times.
func (s *EventStream) Close() {
	s.cancel()
	for range s.ch {
	}
	<-s.done
}
