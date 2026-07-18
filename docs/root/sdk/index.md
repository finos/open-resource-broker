# ORB SDK Overview

The Open Resource Broker ships six official language SDKs. All six implement
the same [hybrid architecture](https://github.com/finos/open-resource-broker/blob/main/sdk/ARCHITECTURE.md): generated typed
models from the OpenAPI spec, plus five hand-written layers for subprocess
management, UNIX domain socket transport, retry, AWS SigV4 auth, and SSE
streaming.

Each SDK covers all 44 operations in the current API spec
(`sdk/spec/openapi.json`).

## Quick-reference table

| Language | Install | Client class | Min runtime |
|----------|---------|-------------|-------------|
| [Python](#python) | `pip install orb-py` | `orb.ORBClient` | Python 3.11 |
| [Go](#go) | `go get github.com/finos/open-resource-broker/sdk/go` | `orb.Client` | Go 1.24 |
| [TypeScript / Node](#typescript--node) | `npm install @finos/open-resource-broker` | `OrbClient` | Node 18 |
| [Java](#java) | `org.finos.openresourcebroker:open-resource-broker-java:0.1.0` | `OrbClient` | Java 17 |
| [Kotlin](#kotlin) | `org.finos.openresourcebroker:open-resource-broker-kotlin:0.1.0` | `OrbClient` | JVM 17 |
| [.NET / C#](#net--c) | `dotnet add package FINOS.OpenResourceBroker` | `OrbClient` | .NET 8 |

For languages not listed here, see the
[Generate Your Own SDK](generating-sdks.md) guide.

---

## Python

**Package:** `orb-py`

```bash
pip install orb-py
# or
uv tool install orb-py
```

**Five-line usage example:**

```python
import asyncio
from orb import ORBClient

async def main():
    async with ORBClient(provider="aws") as client:
        templates = await client.list_templates(active_only=True)
        if not templates:
            print("No templates registered")
            return
        req = await client.create_request(
            template_id=templates[0]["template_id"],
            count=2,
        )
        print(f"Request submitted: {req['created_request_id']}")
        status = await client.get_request(request_id=req["created_request_id"])
        print(f"Status: {status}")

asyncio.run(main())
```

The Python SDK is in-process: it runs inside the ORB server process and calls
CQRS handlers directly rather than going over HTTP. There is no subprocess to
manage.

Full reference: [Python SDK Quickstart](quickstart.md)

---

## Go

**Module:** `github.com/finos/open-resource-broker/sdk/go`

```bash
go get github.com/finos/open-resource-broker/sdk/go@v1.8.3
```

**Five-line usage example:**

```go
import "github.com/finos/open-resource-broker/sdk/go/orb"

c, _ := orb.NewClient(
    orb.WithManagedProcess(orb.ProcessConfig{Binary: "orb"}),
    orb.WithAuth(orb.WithNoAuth()),
)
defer c.Close()

templates, _ := c.ListTemplates(ctx)
mr, _        := c.RequestMachines(ctx, orb.RequestMachinesRequest{
    TemplateID: templates[0].TemplateID,
    Count:      2,
})
final, _ := c.WaitForCompletion(ctx, mr.RequestID)
fmt.Println(final.Status, len(final.Machines))
```

Full reference: [`sdk/go/README.md`](https://github.com/finos/open-resource-broker/blob/main/sdk/go/README.md)

---

## TypeScript / Node

**Package:** `@finos/open-resource-broker`

```bash
npm install @finos/open-resource-broker
```

**Five-line usage example:**

```typescript
import { OrbClient } from "@finos/open-resource-broker";

const client = await OrbClient.create({
    process: { binary: "orb" },
    auth: { type: "none" },
});
const { templates } = await client.listTemplates();
const op = await client.requestMachines({
    templateId: templates[0].templateId,
    count: 2,
});
const final = await client.waitForCompletion(op.requestId);
console.log(final.status, final.machines.length);
await client.close();
```

Full reference: [`sdk/typescript/README.md`](https://github.com/finos/open-resource-broker/blob/main/sdk/typescript/README.md)

---

## Java

**Coordinates:** `org.finos.openresourcebroker:open-resource-broker-java:0.1.0`

Gradle:

```groovy
implementation 'org.finos.openresourcebroker:open-resource-broker-java:0.1.0'
```

Maven:

```xml
<dependency>
  <groupId>org.finos.openresourcebroker</groupId>
  <artifactId>open-resource-broker-java</artifactId>
  <version>0.1.0</version>
</dependency>
```

**Five-line usage example:**

```java
import org.finos.openresourcebroker.sdk.client.OrbClient;
import org.finos.openresourcebroker.sdk.model.*;

try (OrbClient client = OrbClient.builder()
        .process(ProcessConfig.builder().binary("orb").build())
        .build()) {
    TemplateListResponse templates = client.listTemplates(null, null, null);
    RequestOperationResponse op = client.requestMachines(
        new RequestMachinesRequest()
            .templateId(templates.getTemplates().get(0).getTemplateId())
            .count(2));
    StreamEvent final_ = client.waitForCompletion(op.getRequestId());
    System.out.println(final_.getStatus());
}
```

Full reference: [`sdk/java/README.md`](https://github.com/finos/open-resource-broker/blob/main/sdk/java/README.md)

---

## Kotlin

**Coordinates:** `org.finos.openresourcebroker:open-resource-broker-kotlin:0.1.0`

Gradle (Kotlin DSL):

```kotlin
implementation("org.finos.openresourcebroker:open-resource-broker-kotlin:0.1.0")
```

**Five-line usage example:**

```kotlin
import org.finos.openresourcebroker.sdk.client.OrbClient
import org.finos.openresourcebroker.sdk.model.*

val client = OrbClient.create(ClientConfig(process = ProcessConfig("orb")))
val templates = client.listTemplates()
val op = client.requestMachines(
    RequestMachinesRequest(
        templateId = templates.templates.first().templateId,
        count = 2,
    )
)
val final = client.waitForCompletion(op.requestId)
println("${final?.status} — ${final?.machines?.size} machines")
client.close()
```

Full reference: [`sdk/kotlin/README.md`](https://github.com/finos/open-resource-broker/blob/main/sdk/kotlin/README.md)

---

## .NET / C#

**Package:** `FINOS.OpenResourceBroker`

```bash
dotnet add package FINOS.OpenResourceBroker
```

**Five-line usage example:**

```csharp
using FINOS.OpenResourceBroker;
using FINOS.OpenResourceBroker.Models;

await using var client = await OrbClient.CreateAsync(new ClientConfig {
    Process = new ProcessConfig { Binary = "orb" },
});
var templates = await client.ListTemplatesAsync();
var op = await client.RequestMachinesAsync(new RequestMachinesRequest {
    TemplateId = templates.Templates[0].TemplateId,
    Count = 2,
});
var final = await client.WaitForCompletionAsync(op.RequestId);
Console.WriteLine($"{final.Status} — {final.Machines.Count} machines");
```

Full reference: [`sdk/csharp/README.md`](https://github.com/finos/open-resource-broker/blob/main/sdk/csharp/README.md)

---

## Architecture overview

All official SDKs share the same five-layer design. See
[`sdk/ARCHITECTURE.md`](https://github.com/finos/open-resource-broker/blob/main/sdk/ARCHITECTURE.md) for the full specification
of each layer and language-specific implementation notes.

## Cross-language parity

All SDKs are validated against the same canonical scenario defined in
[`sdk/parity/scenario.json`](https://github.com/finos/open-resource-broker/blob/main/sdk/parity/scenario.json). The scenario
covers: health check, list templates, request machines, poll request status,
and return machines. See [`sdk/parity/README.md`](https://github.com/finos/open-resource-broker/blob/main/sdk/parity/README.md)
for how to run parity checks per SDK.

## Building your own SDK for an unsupported language

See [Generating Your Own SDK](generating-sdks.md).
