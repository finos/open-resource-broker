# Symphony Hostfactory K8s Provider Design Decisions (FAQ)

## Q: What is the purpose of this document?
A: This document aims to provide answers to frequently asked questions regarding
the design decisions made for the Symphony Hostfactory K8s provider.

## Q: What is the Symphony Hostfactory K8s provider?
A: The Symphony Hostfactory K8s provider is a custom provider plugin for Symphony
Hostfactory that enables provisioning and deprovisioning of Kubernetes resources
based on Symphony workloads.

## Q: What are the key design decisions for the Hostfactory K8s provider?
A: The key design decisions for the Symphony Hostfactory K8s provider include:
- File-based management of Hostfactory objects (e.g., requests, return requests,
  etc.).
- Multiple asynchronous processes to manage the provisioning/deprovisioning loop
  (e.g., pods watcher process, Hostfactory requests watcher process, etc.).
- Integration with the Kubernetes API, using Pods instead of ReplicaSets.
- Support for dynamic scaling of workloads by implementing the required Symphony
  Hostfactory interfaces that are called by the Symphony Hostfactory requestor
  process.
- Flexible configuration options.
- Additional controls around retries, catch-up mechanisms, and timeouts.
- A cloud service provider (CSP)-agnostic approach.

## Q: Why use filesystem files/symlinks to manage the Hostfactory objects?
A: It is a battle-tested, scalable design pattern to manage and track state. This
design pattern allows the system to run on any POSIX-compliant filesystem,
simplifying local testing, debugging, and portability. The design decouples the
management of Kubernetes objects from Hostfactory-specific constructs while
atomically tracking state on the local filesystem. Additionally, by utilizing
this design pattern, we can leverage inotify file events, which enable
asynchronous, event-driven processing of Hostfactory requestor actions. For
example, when the Hostfactory requestor requests new machines to be added, the
Hostfactory provider API creates the necessary files to track the machine
status, while the request-machines watcher process captures the file creation
events and triggers the required Kubernetes API calls to provision the pods.
Using this design pattern has additional benefits, such as reducing the number
of API calls required for Kubernetes - as we can just query the files locally
instead. This reduces the possibility of API call failures or reaching
API request limits.

## Q: Why use multiple processes instead of one?
A: Multiple processes streamline an event-driven, portable, decoupled, and
resilient architecture. It is a trade-off between simple debugging (with one
process) and a resilient, portable architecture. Processes can be tracked by a
supervisor (either deployed locally or on Kubernetes) and restarted in case of
failure. Processes can also be run locally for testing, and they are all
single-threaded Python processes (we avoid threads to prevent unnecessary
handling of race conditions and other issues with multithreaded applications).
Additionally, Symphony Hostfactory expects a synchronous response when it calls
one of its functions (e.g., request-machines, request-return-machines). Having
multiple processes helps decouple what the Hostfactory provider API returns from
what happens in the background (e.g., provisioning or deprovisioning pods).

## Q: Why create Pods instead of scaling ReplicaSets?
A: While the Kubernetes API is designed to use ReplicaSets to scale pods,
Hostfactory works as a "machine" orchestrator itself and would compete with
ReplicaSets. For example, if a pod fails in a ReplicaSet, it will be replaced by
a new replica that Hostfactory cannot track. This can lead to side effects, such
as additional untracked resources in the cluster, increasing costs. By utilizing
individual pods, we allow Hostfactory to manage the resources with minimal
overhead or competition from controllers like ReplicaSets.

## Q: How does the Hostfactory K8s provider ensure HA and fault tolerance?
A: The Kubernetes provider implements mechanisms such as always returning a
response to the Hostfactory requestor, which allows the Hostfactory loop to
continue functioning. Processes like pods/requests/return-requests watchers have
catch-up mechanisms in case they fail. For example, if the Hostfactory API
requests new machines, the requests watcher process can check the request files
and a processed flag to decide whether it needs to process the request or skip
it.
