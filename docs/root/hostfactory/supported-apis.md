# Host Factory Supported APIs

This document lists AWS API combinations first, followed by OCICompute support
and its current validation scope.


## Supported API Combinations

### Current Implementation Status

| Provider API | Fleet Type | Price Type | Status | Description |
|-------------|------------|------------|---------|-------------|
| **RunInstances** | - | ondemand | <span style="color: blue; font-weight: bold;">IMPLEMENTED</span> |  |
| **RunInstances** | - | spot | <span style="color: red; font-weight: bold;">NOT-SUPPORT</span> | Intentionally not supported to maintain backward compatibility |
|||||
| **SpotFleet** | request | ondemand | <span style="color: blue; font-weight: bold;">IMPLEMENTED</span> |  |
| **SpotFleet** | request | spot | <span style="color: blue; font-weight: bold;">IMPLEMENTED</span> |  |
|||||
| **EC2Fleet** | instant | ondemand | <span style="color: blue; font-weight: bold;">IMPLEMENTED</span> |  |
| **EC2Fleet** | instant | spot | <span style="color: blue; font-weight: bold;">IMPLEMENTED</span> |  |
| **EC2Fleet** | request | ondemand |<span style="color: blue; font-weight: bold;">IMPLEMENTED</span> |  |
| **EC2Fleet** | request | spot | <span style="color: blue; font-weight: bold;">IMPLEMENTED</span> |  |
| **EC2Fleet** | maintain | ondemand |<span style="color: orange; font-weight: bold;">NOT-IMPLEMENTED</span> |  |
| **EC2Fleet** | maintain | spot | <span style="color: orange; font-weight: bold;">NOT-IMPLEMENTED</span> |  |
|||||
| **ASG** | - | ondemand | <span style="color: blue; font-weight: bold;">IMPLEMENTED</span> |  |
| **ASG** | - | spot | <span style="color: orange; font-weight: bold;">NOT-IMPLEMENTED</span> |  |



### Legend

- <span style="color: blue; font-weight: bold;">IMPLEMENTED</span>: Code implemented and tested.
- <span style="color: orange; font-weight: bold;">NOT-IMPLEMENTED</span>: Not yet implemented.
- <span style="color: red; font-weight: bold;">NOT-SUPPORT</span>: Intentionally not supported.

## OCICompute

OCI uses the `OCICompute` provider API for individual VM and bare metal workers.
Capacity selection uses OCI's `capacity_type: ondemand|preemptible`, rather than
the AWS price/fleet combinations above. See the
[OCICompute provider reference](../providers/oci/compute.md) for supported fields
and lifecycle behaviour, and the [deployment guide](../deployment/oci-integration.md)
for initialization with `--scheduler hostfactory`.

HostFactory template mapping, persistence, generation, and dry-run CLI workflows
are covered by automated tests. Configure and validate Symphony worker registration
and workload execution for the target deployment.



