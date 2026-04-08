// Package main demonstrates basic ORB SDK usage: list templates, request
// machines, and wait for the request to reach a terminal status.
//
// Prerequisites:
//
//	orb init          # one-time setup
//	orb system serve  # ORB must be running before this example
//
// Usage:
//
//	go run . --url http://localhost:8000 --template <template-id> --count 2
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/awslabs/open-resource-broker/sdk/go/orb"
)

func main() {
	url := flag.String("url", "http://localhost:8000", "ORB server base URL")
	templateID := flag.String("template", "", "template ID to request (leave empty to list and exit)")
	count := flag.Int("count", 1, "number of machines to request")
	flag.Parse()

	ctx := context.Background()

	c, err := orb.NewClient(
		orb.WithBaseURL(*url),
		orb.WithAuth(orb.WithNoAuth()),
		orb.WithTimeout(60*time.Second),
	)
	if err != nil {
		log.Fatalf("NewClient: %v", err)
	}
	defer c.Close()

	// List available templates.
	templates, err := c.ListTemplates(ctx)
	if err != nil {
		log.Fatalf("ListTemplates: %v", err)
	}
	fmt.Printf("templates (%d):\n", len(templates))
	for _, t := range templates {
		fmt.Printf("  %-30s  %s\n", t.TemplateID, t.Name)
	}

	if *templateID == "" {
		fmt.Println("\npass --template <id> to request machines")
		os.Exit(0)
	}

	// Request machines.
	mr, err := c.RequestMachines(ctx, orb.RequestMachinesRequest{
		TemplateID: *templateID,
		Count:      *count,
	})
	if err != nil {
		log.Fatalf("RequestMachines: %v", err)
	}
	fmt.Printf("\nrequest submitted: %s\n", mr.RequestID)

	// Wait for completion (blocks until terminal status or context cancelled).
	fmt.Println("waiting for completion...")
	final, err := c.WaitForCompletion(ctx, mr.RequestID,
		orb.WithSSEInterval(2*time.Second),
		orb.WithSSETimeout(10*time.Minute),
	)
	if err != nil {
		log.Fatalf("WaitForCompletion: %v", err)
	}

	fmt.Printf("status=%s  requested=%d  successful=%d  failed=%d\n",
		final.Status, final.RequestedCount, final.SuccessfulCount, final.FailedCount)
	for _, m := range final.Machines {
		fmt.Printf("  machine=%s  ip=%s  status=%s\n", m.MachineID, m.PrivateIP, m.Status)
	}
}
