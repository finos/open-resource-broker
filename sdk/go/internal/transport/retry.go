package transport

import (
	"errors"
	"io"
	"net"
	"net/http"
	"syscall"
	"time"
)

// noRetryHeader, when present on an outgoing request, disables all retry logic
// for that single request. The header is stripped before the request is sent.
// Used by Health(), which must observe a 503 (degraded) response directly
// rather than retry-looping on it.
const noRetryHeader = "X-Orb-No-Retry"

// DisableRetry marks req so RetryTransport will not retry it. Safe to call on
// any *http.Request before it is sent.
func DisableRetry(req *http.Request) {
	req.Header.Set(noRetryHeader, "1")
}

// RetryTransport retries idempotent requests on transient errors.
// It retries on: idempotent-method (GET/PUT/DELETE/HEAD) network errors, 429, 503.
// It never retries: non-idempotent methods (POST) — neither on 429/503 nor on
// post-write network errors, because a provisioning POST may already have been
// processed server-side before the socket dropped, and a blind retry risks
// silent double-provisioning. 4xx (except 429) is never retried.
type RetryTransport struct {
	Next       http.RoundTripper
	MaxRetries int
	BaseDelay  time.Duration
}

// NewRetryTransport wraps next with retry logic.
// maxRetries defaults to 3, baseDelay defaults to 500ms if zero.
func NewRetryTransport(next http.RoundTripper, maxRetries int, baseDelay time.Duration) *RetryTransport {
	if maxRetries <= 0 {
		maxRetries = 3
	}
	if baseDelay <= 0 {
		baseDelay = 500 * time.Millisecond
	}
	return &RetryTransport{Next: next, MaxRetries: maxRetries, BaseDelay: baseDelay}
}

func (t *RetryTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// Per-request opt-out (stripped before the request leaves the SDK).
	if req.Header.Get(noRetryHeader) != "" {
		req.Header.Del(noRetryHeader)
		return t.Next.RoundTrip(req)
	}

	var (
		resp *http.Response
		err  error
	)
	for attempt := 0; attempt <= t.MaxRetries; attempt++ {
		if attempt > 0 {
			delay := t.BaseDelay * (1 << (attempt - 1)) // exponential: 500ms, 1s, 2s
			select {
			case <-req.Context().Done():
				return nil, req.Context().Err()
			case <-time.After(delay):
			}

			// Rewind the request body before re-sending. The first attempt
			// drains req.Body to EOF (the base transport, and SigV4Transport's
			// io.ReadAll, consume the shared reader), so retrying with the same
			// *http.Request would send a zero-length body. GetBody, which
			// net/http sets for in-memory bodies (e.g. bytes.NewReader in
			// client.put), yields a fresh reader positioned at the start.
			// Because RetryTransport is the outermost transport in the chain,
			// resetting here guarantees every downstream attempt — including the
			// SigV4Transport clone — sees the full body.
			if req.GetBody != nil {
				body, gbErr := req.GetBody()
				if gbErr != nil {
					return nil, gbErr
				}
				req.Body = body
			}
		}

		resp, err = t.Next.RoundTrip(req)
		if err != nil {
			if shouldRetryNetworkError(req.Method, err) {
				continue
			}
			return nil, err
		}

		// Success or non-retryable HTTP status
		if !shouldRetryStatus(req.Method, resp.StatusCode) {
			return resp, nil
		}

		// Drain and close body before retry
		resp.Body.Close()
	}
	return resp, err
}

// isIdempotent reports whether an HTTP method may be safely retried after a
// transient failure. POST is excluded: a provisioning POST may already have
// been applied server-side before the failure, so retrying risks duplicating
// the operation.
func isIdempotent(method string) bool {
	switch method {
	case http.MethodGet, http.MethodPut, http.MethodDelete, http.MethodHead:
		return true
	default:
		return false
	}
}

func shouldRetryStatus(method string, status int) bool {
	// 429/503 and other 5xx are retried ONLY for idempotent methods. A POST is
	// never retried on these — the server may have processed it before failing.
	if !isIdempotent(method) {
		return false
	}
	switch status {
	case 429, 503:
		return true
	}
	if status >= 500 {
		return true
	}
	return false
}

// shouldRetryNetworkError decides whether a transport-level error is retryable.
//
//   - Idempotent methods: retry any net.Error except io.EOF (a bare EOF means
//     the server closed the connection; retrying just hits another dead socket).
//   - POST: retry ONLY on a pre-write connection failure (connection refused),
//     which guarantees the server never received the request. Post-write
//     failures (timeouts, resets, EOF) are NOT retried, because the request may
//     already have been processed — retrying risks silent double-provisioning.
func shouldRetryNetworkError(method string, err error) bool {
	if errors.Is(err, io.EOF) {
		return false
	}
	var netErr net.Error
	if !errors.As(err, &netErr) {
		return false
	}
	if isIdempotent(method) {
		return true
	}
	// Non-idempotent (POST): only safe when we know the request never reached
	// the server, i.e. the connection was refused before any bytes were written.
	return isConnectionRefused(err)
}

// isConnectionRefused reports whether err is a pre-write connection-refused
// failure (ECONNREFUSED), the only network error safe to retry for a POST.
func isConnectionRefused(err error) bool {
	return errors.Is(err, syscall.ECONNREFUSED)
}
