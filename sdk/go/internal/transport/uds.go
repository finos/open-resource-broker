// Layer 2: UDS Transport
package transport

import (
	"context"
	"net"
	"net/http"
)

// UnixSocket returns an http.RoundTripper that dials via a Unix domain socket.
// The host portion of the URL is ignored; all connections go to socketPath.
func UnixSocket(socketPath string) http.RoundTripper {
	return &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
		},
		DisableKeepAlives:   true, // uvicorn closes connections after each request over UDS
		ForceAttemptHTTP2:   false,
		MaxIdleConns:        0,
		MaxIdleConnsPerHost: 0,
	}
}
