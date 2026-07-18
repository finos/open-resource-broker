package orb

import (
	"context"
	"errors"
	"fmt"
)

var (
	ErrNotFound       = errors.New("orb: not found")
	ErrUnauthorized   = errors.New("orb: unauthorized")
	ErrForbidden      = errors.New("orb: forbidden")
	ErrConflict       = errors.New("orb: conflict")
	ErrORBUnavailable = errors.New("orb: service unavailable")
	ErrTimeout        = errors.New("orb: request timeout")
)

// OrbError is the base type for every error the SDK returns. It mirrors the
// OrbError base exposed by the TypeScript, Kotlin and C# SDKs so the whole
// family shares one vocabulary. Use errors.Is(err, orb.ErrNotFound) etc. for
// status-based checks; the sentinel is carried on the embedded OrbError.
type OrbError struct {
	Message  string
	sentinel error
}

func (e *OrbError) Error() string { return e.Message }

func (e *OrbError) Unwrap() error { return e.sentinel }

func (e *OrbError) Is(target error) bool {
	return e.sentinel != nil && errors.Is(e.sentinel, target)
}

// OrbApiError is returned for all HTTP error responses from ORB. It carries the
// canonical cross-SDK field set: HTTP status, machine-readable error code,
// message, and the server request ID (for support/correlation).
//
// Use errors.Is(err, orb.ErrNotFound) etc. for status-based checks and
// errors.As(err, &apiErr) to access StatusCode, Code, RequestID, Message.
type OrbApiError struct {
	OrbError
	StatusCode int
	Code       string
	RequestID  string
	Details    any
}

func (e *OrbApiError) Error() string {
	if e.Code != "" {
		return fmt.Sprintf("orb: HTTP %d %s: %s", e.StatusCode, e.Code, e.Message)
	}
	return fmt.Sprintf("orb: HTTP %d: %s", e.StatusCode, e.Message)
}

// mapError converts network-level errors (context cancellation, dial failures)
// into typed OrbApiError values. HTTP-level errors (4xx, 5xx) are handled by
// parseAPIError in client.go which reads the response body directly.
func mapError(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		return &OrbApiError{OrbError: OrbError{sentinel: ErrTimeout, Message: err.Error()}}
	}
	// Network errors (dial failure, connection reset, etc.)
	// Wrap in OrbApiError with ErrORBUnavailable sentinel
	var netErr interface{ Timeout() bool }
	if errors.As(err, &netErr) {
		return &OrbApiError{OrbError: OrbError{sentinel: ErrORBUnavailable, Message: err.Error()}}
	}
	return err
}

func sentinelForStatus(code int) error {
	switch code {
	case 404:
		return ErrNotFound
	case 401:
		return ErrUnauthorized
	case 403:
		return ErrForbidden
	case 409:
		return ErrConflict
	case 503:
		return ErrORBUnavailable
	case 408:
		return ErrTimeout
	default:
		return nil
	}
}
