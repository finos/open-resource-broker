# open-resource-broker-kotlin

Kotlin client SDK for the
[Open Resource Broker](https://github.com/finos/open-resource-broker) (ORB),
published as `org.finos.open-resource-broker:open-resource-broker-kotlin`.

## Prerequisites

ORB is a Python service. Install it before using managed-process mode:

```bash
uv tool install 'orb-py>=1.8.3,<2.0.0'
# or: pip install 'orb-py>=1.8.3,<2.0.0'
```

Verify: `orb --version`

Then run one-time setup (skip if connecting to an existing server):

```bash
orb init
```

## Requirements

- JVM 17 or later
- Kotlin coroutines (`kotlinx.coroutines`)

## Installation

Replace `VERSION` with the latest release listed on the
[releases page](https://github.com/finos/open-resource-broker/releases).

Gradle (Kotlin DSL):

```kotlin
implementation("org.finos.open-resource-broker:open-resource-broker-kotlin:VERSION")
```

Gradle (Groovy DSL):

```groovy
implementation 'org.finos.open-resource-broker:open-resource-broker-kotlin:VERSION'
```

## Quick Start

```kotlin
import kotlinx.coroutines.runBlocking
import org.finos.openresourcebroker.sdk.client.*
import org.finos.openresourcebroker.sdk.process.ProcessConfig

runBlocking {
    val client = OrbClient.create(ClientConfig(process = ProcessConfig(binary = "orb")))
    println(client.listTemplates().templates.size)
    client.close()
}
```

## IPC Model

The SDK communicates with ORB over a **Unix domain socket** (UDS) backed by
OkHttp with a custom `SocketFactory`. When configured for managed-process mode
the SDK spawns ORB as a child process and routes all HTTP traffic through the
socket — no TCP port is allocated.

## Operating Modes

### Spawn mode (recommended)

```kotlin
import org.finos.openresourcebroker.sdk.client.*
import org.finos.openresourcebroker.sdk.process.ProcessConfig

val client = OrbClient.create(
    ClientConfig(process = ProcessConfig(binary = "orb"))
)
// use client ...
client.close()
```

### Remote mode

```kotlin
val client = OrbClient.create(
    ClientConfig(
        baseUrl = "https://orb.example.com",
        auth = AuthOption.Bearer("my-token"),
    )
)
```

## Usage

```kotlin
import kotlinx.coroutines.runBlocking
import org.finos.openresourcebroker.sdk.client.*
import org.finos.openresourcebroker.sdk.model.*
import org.finos.openresourcebroker.sdk.process.ProcessConfig

runBlocking {
    val client = OrbClient.create(
        ClientConfig(process = ProcessConfig(binary = "orb"))
    )

    // List templates
    val templates = client.listTemplates()
    if (templates.templates.isEmpty()) {
        println("No templates registered")
        return@runBlocking
    }

    // Request machines
    val op = client.requestMachines(
        RequestMachinesRequest(
            templateId = templates.templates.first().templateId,
            count = 2,
        )
    )
    println("Request submitted: ${op.requestId}")

    // Wait for completion (collects the SSE stream until terminal status)
    val final = client.waitForCompletion(op.requestId)
    println("Status: ${final?.status}, machines: ${final?.machines?.size}")

    // Return machines
    val machineIds = final?.machines?.map { it.machineId } ?: emptyList()
    if (machineIds.isNotEmpty()) {
        client.returnMachines(ReturnMachinesRequest(machineIds = machineIds))
    }

    client.close()
}
```

## Authentication

| Mode | `AuthOption` |
|------|-------------|
| None | `AuthOption.None` |
| Bearer token | `AuthOption.Bearer("token")` |
| AWS SigV4 | `AuthOption.SigV4(region = "us-east-1", service = "execute-api")` |

```kotlin
val client = OrbClient.create(
    ClientConfig(
        baseUrl = "https://orb.example.com",
        auth = AuthOption.Bearer("my-token"),
    )
)
```

## Scheduler (HostFactory mode)

```kotlin
val client = OrbClient.create(
    ClientConfig(
        process = ProcessConfig(binary = "orb"),
        scheduler = Scheduler.HOSTFACTORY,
    )
)
```

When `scheduler = Scheduler.HOSTFACTORY` is set, the SDK appends
`X-ORB-Scheduler: hostfactory` to every request header.

## Streaming

```kotlin
// Flow-based SSE stream with reconnect
client.streamRequestStatus(requestId)
    .collect { event -> println(event.status) }

// Convenience — blocks until terminal status
val final = client.waitForCompletion(requestId)
```

## Error Handling

```kotlin
import org.finos.openresourcebroker.sdk.client.OrbApiError

try {
    client.getTemplate("unknown")
} catch (e: OrbApiError) {
    println("HTTP ${e.statusCode}: ${e.message}")
}
```

## Version Compatibility

The SDK requires the ORB Python service (`orb-py`) version 1.8.3 or later and
earlier than 2.0.0.

## Documentation

Full documentation, including the SDK overview and the API reference, is at
[https://finos.github.io/open-resource-broker/](https://finos.github.io/open-resource-broker/).
The SDK overview covers all supported languages:
[https://finos.github.io/open-resource-broker/sdk/](https://finos.github.io/open-resource-broker/sdk/).

Instructions for building and testing the SDKs from source are in the
[developer testing guide](https://finos.github.io/open-resource-broker/developer_guide/testing/#sdk-development).

## License

Licensed under the Apache License, Version 2.0. See the
[LICENSE](https://github.com/finos/open-resource-broker/blob/main/LICENSE) file.

Open Resource Broker is a project of the
[Fintech Open Source Foundation (FINOS)](https://www.finos.org/).
