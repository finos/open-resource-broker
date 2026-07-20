package orb

import (
	"context"
	"fmt"
	"net/http"
)

// AuthOption is the interface for authentication strategies.
type AuthOption interface {
	authOption
}

// authOption is the unexported interface implemented by all auth strategies.
type authOption interface {
	wrap(next http.RoundTripper) http.RoundTripper
}

// WithNoAuth disables authentication (default).
func WithNoAuth() AuthOption {
	return noAuth{}
}

// WithBearerToken authenticates with a static Bearer token.
func WithBearerToken(token string) AuthOption {
	return WithBearerTokenFunc(func(_ context.Context) (string, error) {
		return token, nil
	})
}

// WithBearerTokenFunc authenticates with a dynamic Bearer token.
// The function is called on every request, enabling token refresh.
func WithBearerTokenFunc(fn func(ctx context.Context) (string, error)) AuthOption {
	return bearerAuth{tokenFn: fn}
}

// WithCustomAuth creates an AuthOption from an arbitrary signing function.
// fn receives a clone of the outgoing request and may mutate its headers freely.
// Use this to implement auth strategies not provided by the SDK — for example
// Azure Workload Identity, GCP service-account tokens, or OIDC exchange flows.
//
// Example:
//
//	client := orb.New(orb.WithCustomAuth(func(req *http.Request) error {
//	    token, err := myTokenProvider.GetToken(req.Context())
//	    if err != nil {
//	        return fmt.Errorf("custom auth: %w", err)
//	    }
//	    req.Header.Set("Authorization", "Bearer "+token)
//	    return nil
//	}))
func WithCustomAuth(fn func(req *http.Request) error) AuthOption {
	return customAuth{fn: fn}
}

// customAuth wraps an arbitrary signing function as an authOption.
type customAuth struct {
	fn func(req *http.Request) error
}

func (a customAuth) wrap(next http.RoundTripper) http.RoundTripper {
	return &customTransport{fn: a.fn, next: next}
}

type customTransport struct {
	fn   func(req *http.Request) error
	next http.RoundTripper
}

func (t *customTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	req = req.Clone(req.Context())
	if err := t.fn(req); err != nil {
		return nil, fmt.Errorf("custom auth: %w", err)
	}
	return t.next.RoundTrip(req)
}

// noAuth is a pass-through transport.
type noAuth struct{}

func (noAuth) wrap(next http.RoundTripper) http.RoundTripper { return next }

// bearerAuth adds Authorization: Bearer <token> to every request.
type bearerAuth struct {
	tokenFn func(ctx context.Context) (string, error)
}

func (a bearerAuth) wrap(next http.RoundTripper) http.RoundTripper {
	return &bearerTransport{tokenFn: a.tokenFn, next: next}
}

type bearerTransport struct {
	tokenFn func(ctx context.Context) (string, error)
	next    http.RoundTripper
}

func (t *bearerTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	req = req.Clone(req.Context())
	token, err := t.tokenFn(req.Context())
	if err != nil {
		return nil, fmt.Errorf("bearer: getting token: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	return t.next.RoundTrip(req)
}
