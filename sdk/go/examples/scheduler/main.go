// Package main demonstrates WithScheduler(SchedulerHostFactory): the SDK
// switches to camelCase JSON serialisation to match the HostFactory scheduler.
//
// Prerequisites:
//
//	pip install 'orb-py>=1.5.2,<2.0.0'
//	orb init
//
// Usage:
//
//	go run . --template <template-id>
//
// The ORB server must be started with --scheduler hostfactory:
//
//	orb system serve --scheduler hostfactory
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
	count := flag.Int("count", 1, "number of machines to request")
	flag.Parse()

	ctx := context.Background()

	// WithManagedProcess passes --scheduler hostfactory to the ORB subprocess
	// automatically when WithScheduler(SchedulerHostFactory) is set.
	c, err := orb.NewClient(
		orb.WithManagedProcess(orb.ProcessConfig{
			Binary:       "orb",
			StartTimeout: 30 * time.Second,
		}),
		orb.WithScheduler(orb.SchedulerHostFactory),
		orb.WithAuth(orb.WithNoAuth()),
	)
	if err != nil {
		log.Fatalf("NewClient: %v", err)
	}
	defer c.Close()

	fmt.Printf("scheduler: hostfactory  healthy: %v\n", c.Healthy())

	templates, err := c.ListTemplates(ctx)
	if err != nil {
		log.Fatalf("ListTemplates: %v", err)
	}
	fmt.Printf("templates: %d\n", len(templates))
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
	fmt.Printf("request: %s\n", mr.RequestID)

	// Stream events manually to show per-event progress.
	stream, err := c.StreamRequest(ctx, mr.RequestID)
	if err != nil {
		log.Fatalf("StreamRequest: %v", err)
	}
	defer stream.Close()

	for {
		event, ok := stream.Next()
		if !ok {
			break
		}
		fmt.Printf("  status=%-12s  successful=%d  failed=%d\n",
			event.Status, event.SuccessfulCount, event.FailedCount)
	}
	if err := stream.Err(); err != nil {
		log.Fatalf("stream error: %v", err)
	}
}
