// Package orb provides a Go client for the Open Resource Broker (ORB) API.
//
// ORB provisions cloud compute resources. This client can start and manage a
// local ORB server process, connect to a remote ORB endpoint, or connect to a
// server listening on a Unix domain socket. Requests are retried on transient
// failures, and AWS SigV4 and bearer-token authentication are supported.
//
// # Installation
//
//	go get github.com/finos/open-resource-broker/sdk/go
//
// # Prerequisites
//
// Go 1.24 or later. In managed-process mode the ORB service must be installed
// and available on PATH as "orb":
//
//	uv tool install 'orb-py>=1.8.3,<2.0.0'
//	orb init
//
// Connecting to an existing ORB server needs no local installation.
//
// # Usage
//
// Start ORB as a managed process, list templates, request machines and wait for
// the request to finish:
//
//	c, err := orb.NewClient(
//	    orb.WithManagedProcess(orb.ProcessConfig{Binary: "orb"}),
//	    orb.WithAuth(orb.WithNoAuth()),
//	)
//	if err != nil {
//	    log.Fatal(err)
//	}
//	defer c.Close()
//
//	ctx := context.Background()
//	templates, err := c.ListTemplates(ctx)
//	if err != nil {
//	    log.Fatal(err)
//	}
//	mr, err := c.RequestMachines(ctx, orb.RequestMachinesRequest{
//	    TemplateID: templates[0].TemplateID,
//	    Count:      2,
//	})
//	if err != nil {
//	    log.Fatal(err)
//	}
//	final, err := c.WaitForCompletion(ctx, mr.RequestID)
//	if err != nil {
//	    log.Fatal(err)
//	}
//	fmt.Println(final.Status, len(final.Machines))
//
// To connect to a running server instead, use WithBaseURL or WithUnixSocket in
// place of WithManagedProcess.
//
// Full documentation: https://finos.github.io/open-resource-broker/sdk/
//
// Licensed under the Apache License, Version 2.0.
package orb
