// Package main demonstrates managed-process mode: the SDK starts ORB
// automatically via a Unix domain socket and stops it on exit.
//
// Prerequisites:
//
//	pip install 'orb-py>=1.5.2,<2.0.0'   # or: uv tool install orb-py
//	orb init                               # one-time setup
//
// Usage:
//
//	go run . --template <template-id>
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"time"

	"github.com/awslabs/open-resource-broker/sdk/go/orb"
)

func main() {
	templateID := flag.String("template", "", "template ID to request")
	socketPath := flag.String("socket", "", "explicit socket path (default: auto-generated)")
	count := flag.Int("count", 1, "number of machines to request")
	flag.Parse()

	ctx := context.Background()

	pcfg := orb.ProcessConfig{
		Binary:       "orb",
		SocketPath:   *socketPath, // empty = auto /tmp/orb-<pid>.sock
		StartTimeout: 30 * time.Second,
		StopTimeout:  10 * time.Second,
	}

	c, err := orb.NewClient(
		orb.WithManagedProcess(pcfg),
		orb.WithAuth(orb.WithNoAuth()),
	)
	if err != nil {
		log.Fatalf("NewClient: %v", err)
	}
	defer c.Close() // sends SIGTERM to ORB subprocess

	fmt.Printf("ORB healthy: %v\n", c.Healthy())

	templates, err := c.ListTemplates(ctx)
	if err != nil {
		log.Fatalf("ListTemplates: %v", err)
	}
	fmt.Printf("templates available: %d\n", len(templates))
	for _, t := range templates {
		fmt.Printf("  %s  %s\n", t.TemplateID, t.Name)
	}

	if *templateID == "" {
		fmt.Println("pass --template <id> to request machines")
		return
	}

	mr, err := c.RequestMachines(ctx, orb.RequestMachinesRequest{
		TemplateID: *templateID,
		Count:      *count,
	})
	if err != nil {
		log.Fatalf("RequestMachines: %v", err)
	}
	fmt.Printf("request submitted: %s\n", mr.RequestID)

	final, err := c.WaitForCompletion(ctx, mr.RequestID)
	if err != nil {
		log.Fatalf("WaitForCompletion: %v", err)
	}
	fmt.Printf("status=%s  successful=%d  failed=%d\n",
		final.Status, final.SuccessfulCount, final.FailedCount)
	for _, m := range final.Machines {
		fmt.Printf("  machine=%s  ip=%s\n", m.MachineID, m.PrivateIP)
	}
}
