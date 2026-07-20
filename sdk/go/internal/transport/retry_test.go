package transport

import (
	"bytes"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

// rtFunc adapts a function to http.RoundTripper.
type rtFunc func(*http.Request) (*http.Response, error)

func (f rtFunc) RoundTrip(req *http.Request) (*http.Response, error) { return f(req) }

func newResp(status int) *http.Response {
	return &http.Response{
		StatusCode: status,
		Body:       io.NopCloser(strings.NewReader("")),
		Header:     make(http.Header),
	}
}

func newReq(t *testing.T, method string) *http.Request {
	t.Helper()
	req, err := http.NewRequest(method, "http://localhost/x", nil)
	if err != nil {
		t.Fatal(err)
	}
	return req
}

// mockNetErr is a net.Error that is not a connection-refused error.
type mockNetErr struct{}

func (mockNetErr) Error() string   { return "mock net error" }
func (mockNetErr) Timeout() bool   { return true }
func (mockNetErr) Temporary() bool { return true }

func TestRetryStatus_IdempotentRetriesUntilExhausted(t *testing.T) {
	var calls atomic.Int32
	rt := &RetryTransport{
		Next: rtFunc(func(*http.Request) (*http.Response, error) {
			calls.Add(1)
			return newResp(503), nil
		}),
		MaxRetries: 3,
		BaseDelay:  time.Millisecond,
	}
	resp, err := rt.RoundTrip(newReq(t, http.MethodGet))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.StatusCode != 503 {
		t.Fatalf("expected final 503, got %d", resp.StatusCode)
	}
	if got := calls.Load(); got != 4 { // initial + 3 retries
		t.Fatalf("expected 4 attempts for idempotent 503, got %d", got)
	}
}

func TestRetryStatus_PostNeverRetriedOn503(t *testing.T) {
	var calls atomic.Int32
	rt := &RetryTransport{
		Next: rtFunc(func(*http.Request) (*http.Response, error) {
			calls.Add(1)
			return newResp(503), nil
		}),
		MaxRetries: 3,
		BaseDelay:  time.Millisecond,
	}
	resp, err := rt.RoundTrip(newReq(t, http.MethodPost))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.StatusCode != 503 {
		t.Fatalf("expected 503, got %d", resp.StatusCode)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("POST must not be retried on 503; expected 1 attempt, got %d", got)
	}
}

func TestRetryStatus_PostNeverRetriedOn429(t *testing.T) {
	var calls atomic.Int32
	rt := &RetryTransport{
		Next: rtFunc(func(*http.Request) (*http.Response, error) {
			calls.Add(1)
			return newResp(429), nil
		}),
		MaxRetries: 3,
		BaseDelay:  time.Millisecond,
	}
	if _, err := rt.RoundTrip(newReq(t, http.MethodPost)); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("POST must not be retried on 429; expected 1 attempt, got %d", got)
	}
}

func TestRetryStatus_4xxNotRetried(t *testing.T) {
	var calls atomic.Int32
	rt := &RetryTransport{
		Next: rtFunc(func(*http.Request) (*http.Response, error) {
			calls.Add(1)
			return newResp(404), nil
		}),
		MaxRetries: 3,
		BaseDelay:  time.Millisecond,
	}
	if _, err := rt.RoundTrip(newReq(t, http.MethodGet)); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("404 must not be retried; expected 1 attempt, got %d", got)
	}
}

func TestRetryNetwork_PostNotRetriedOnPostWriteError(t *testing.T) {
	var calls atomic.Int32
	rt := &RetryTransport{
		Next: rtFunc(func(*http.Request) (*http.Response, error) {
			calls.Add(1)
			return nil, mockNetErr{} // timeout/reset — could be post-write
		}),
		MaxRetries: 3,
		BaseDelay:  time.Millisecond,
	}
	if _, err := rt.RoundTrip(newReq(t, http.MethodPost)); err == nil {
		t.Fatal("expected error")
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("POST must not be retried on a post-write network error; expected 1 attempt, got %d", got)
	}
}

func TestRetryNetwork_PostRetriedOnConnectionRefused(t *testing.T) {
	var calls atomic.Int32
	rt := &RetryTransport{
		Next: rtFunc(func(*http.Request) (*http.Response, error) {
			calls.Add(1)
			// Wrap ECONNREFUSED in a net.OpError, mirroring the real dialer.
			return nil, &net.OpError{Op: "dial", Err: syscall.ECONNREFUSED}
		}),
		MaxRetries: 2,
		BaseDelay:  time.Millisecond,
	}
	if _, err := rt.RoundTrip(newReq(t, http.MethodPost)); err == nil {
		t.Fatal("expected error")
	}
	if got := calls.Load(); got != 3 { // pre-write connection-refused is safe to retry
		t.Fatalf("POST should retry on connection-refused; expected 3 attempts, got %d", got)
	}
}

func TestRetryNetwork_GetRetriedButNotOnEOF(t *testing.T) {
	// io.EOF is not retryable even for GET.
	var eofCalls atomic.Int32
	rtEOF := &RetryTransport{
		Next: rtFunc(func(*http.Request) (*http.Response, error) {
			eofCalls.Add(1)
			return nil, io.EOF
		}),
		MaxRetries: 3,
		BaseDelay:  time.Millisecond,
	}
	if _, err := rtEOF.RoundTrip(newReq(t, http.MethodGet)); !errors.Is(err, io.EOF) {
		t.Fatalf("expected io.EOF, got %v", err)
	}
	if got := eofCalls.Load(); got != 1 {
		t.Fatalf("EOF must not be retried; expected 1 attempt, got %d", got)
	}

	// A generic net.Error IS retried for GET.
	var netCalls atomic.Int32
	rtNet := &RetryTransport{
		Next: rtFunc(func(*http.Request) (*http.Response, error) {
			netCalls.Add(1)
			return nil, mockNetErr{}
		}),
		MaxRetries: 2,
		BaseDelay:  time.Millisecond,
	}
	if _, err := rtNet.RoundTrip(newReq(t, http.MethodGet)); err == nil {
		t.Fatal("expected error")
	}
	if got := netCalls.Load(); got != 3 {
		t.Fatalf("GET should retry net.Error; expected 3 attempts, got %d", got)
	}
}

// TestRetry_RewindsBodyOnRetry verifies that a bodied idempotent request (PUT)
// is re-sent with its full body on retry. The first attempt drains the body
// (mimicking the base transport / SigV4's io.ReadAll of the shared reader); a
// correct RetryTransport rewinds via req.GetBody so the second attempt still
// receives every byte. Regression test for empty-body-on-retry.
func TestRetry_RewindsBodyOnRetry(t *testing.T) {
	const payload = `{"value":"important-config"}`

	var attempts atomic.Int32
	var seen [][]byte

	rt := &RetryTransport{
		Next: rtFunc(func(req *http.Request) (*http.Response, error) {
			n := attempts.Add(1)
			// Drain the body exactly as a real downstream transport would.
			var b []byte
			if req.Body != nil {
				var err error
				b, err = io.ReadAll(req.Body)
				if err != nil {
					t.Fatalf("attempt %d: reading body: %v", n, err)
				}
				req.Body.Close()
			}
			seen = append(seen, b)
			if n == 1 {
				return newResp(503), nil // retryable -> triggers a retry
			}
			return newResp(200), nil
		}),
		MaxRetries: 3,
		BaseDelay:  time.Millisecond,
	}

	req, err := http.NewRequest(http.MethodPut, "http://localhost/api/v1/config/key", bytes.NewReader([]byte(payload)))
	if err != nil {
		t.Fatal(err)
	}
	// Sanity: net/http must set GetBody for an in-memory *bytes.Reader body,
	// otherwise the rewind cannot work.
	if req.GetBody == nil {
		t.Fatal("expected GetBody to be set for a bytes.Reader body")
	}

	resp, err := rt.RoundTrip(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.StatusCode != 200 {
		t.Fatalf("expected final 200, got %d", resp.StatusCode)
	}
	if got := attempts.Load(); got != 2 {
		t.Fatalf("expected 2 attempts (503 then 200), got %d", got)
	}
	if len(seen) != 2 {
		t.Fatalf("expected 2 recorded bodies, got %d", len(seen))
	}
	for i, b := range seen {
		if string(b) != payload {
			t.Fatalf("attempt %d received body %q, want %q", i+1, string(b), payload)
		}
	}
}

func TestDisableRetry_SkipsRetryAndStripsHeader(t *testing.T) {
	var calls atomic.Int32
	var sawHeader bool
	rt := &RetryTransport{
		Next: rtFunc(func(req *http.Request) (*http.Response, error) {
			calls.Add(1)
			if req.Header.Get(noRetryHeader) != "" {
				sawHeader = true
			}
			return newResp(503), nil
		}),
		MaxRetries: 3,
		BaseDelay:  time.Millisecond,
	}
	req := newReq(t, http.MethodGet)
	DisableRetry(req)
	if _, err := rt.RoundTrip(req); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("DisableRetry should prevent retries; expected 1 attempt, got %d", got)
	}
	if sawHeader {
		t.Fatal("noRetryHeader must be stripped before the request is sent")
	}
}
