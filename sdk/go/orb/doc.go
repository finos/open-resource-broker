// Package orb provides a Go client for the Open Resource Broker (ORB) API.
//
// The client is built from five hand-written layers (see sdk/ARCHITECTURE.md):
//
//	Layer 1: Subprocess Manager  — internal/process/manager.go
//	Layer 2: UDS Transport       — internal/transport/uds.go
//	Layer 3: Retry Transport     — internal/transport/retry.go
//	Layer 4: SigV4 Auth          — internal/transport/sigv4.go
//	Layer 5: SSE Reader          — internal/sse/reader.go
//
// Basic usage:
//
//	c, err := orb.NewClient(
//	    orb.WithBaseURL("http://localhost:8000"),
//	    orb.WithAuth(orb.WithNoAuth()),
//	)
//	if err != nil {
//	    log.Fatal(err)
//	}
//	defer c.Close()
package orb
