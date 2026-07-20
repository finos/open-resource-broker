# SDK Cross-Language Parity

This directory defines the canonical parity scenario that every official ORB
SDK is expected to execute identically, proving behavioral equivalence across
languages.

## Enforcement status

| Guard | Status | Where |
|-------|--------|-------|
| **Static conformance** — every scenario step's (method, path, operationId) exists in `sdk/spec/openapi.json`, and every `sdk_methods.<lang>` snippet resolves to a real client method | **Enforced in CI** | `make sdk-spec-conformance` → `dev-tools/quality/validate_sdk_spec_conformance.py`, run by the `SDK Spec Conformance` job in `sdk.yml` and gated before every publish job in `prod-release.yml` |
| **Per-language runtime parity** — each SDK drives the scenario against a live ORB and asserts equivalent outcomes | **Enforced in CI** | `make sdk-<lang>-parity` (aggregate `make sdk-parity`), run per matrix leg in `sdk.yml` (the `Run <lang> SDK parity test` steps) |

Both guards are now enforced. The static check guarantees the scenario never
references an operation the spec does not define, nor a client method that does
not exist. The runtime, per-language parity tests LOAD this fixture and drive
its six steps against a real ORB over a UDS, reusing each SDK's contract-test
orb-spawn harness, and assert the per-step expected status/shape plus the skip
rules.

## What is parity testing?

Parity testing verifies that all six official SDKs (Python, Go, TypeScript,
Java, Kotlin, .NET) behave the same way when connected to the same ORB server.
It is distinct from unit tests (which mock the server) and from contract tests
(which exercise every operation). Parity tests exercise a representative
end-to-end scenario and assert that results are equivalent across languages.

## Contents

| File | Purpose |
|------|---------|
| `scenario.json` | Language-agnostic fixture: ordered steps, SDK method names per language, inputs, and expected shapes |
| `README.md` | This file — explains how each SDK's parity test consumes the fixture |

## The canonical scenario

The fixture defines six steps:

| Step | Operation | operationId |
|------|-----------|-------------|
| 1 | Health check | `healthCheck` |
| 2 | List templates | `listTemplates` |
| 3 | Request machines (conditional) | `requestMachines` |
| 4 | Poll request status (conditional) | `getRequestStatus` |
| 5 | Return machines (conditional) | `returnMachines` |
| 6 | List requests | `listRequests` |

Steps 3–5 are conditional: they run only when step 2 returns at least one
template. Step 5 also requires step 4 to have returned at least one machine
ID.

## How each SDK's parity test consumes the fixture

Each SDK's parity test must:

1. Parse `sdk/parity/scenario.json`.
2. Start an ORB instance (spawn mode or point `ORB_TEST_URL` at a running one).
3. Execute the steps in order, using the `sdk_methods.<language>` entry for
   each step to call the correct SDK method.
4. For each step:
   - Assert the HTTP status code is one of the values in `expected.http_status`.
   - Assert the response contains the fields listed in
     `expected.response_shape.required_fields` (or
     `required_fields_one_of` — any one of the listed alternatives satisfies
     the assertion).
   - Bind variables listed in `post_condition.bind` for use in later steps.
   - If `precondition` is not met, mark the step as skipped (not failed).
5. Report the results: each step is PASS, SKIP, or FAIL.
6. The test suite passes if no step is FAIL.

### Avoiding route-level errors

A route-level 404 (the URL path itself does not exist on the server) or a 405
(Method Not Allowed) indicates a client-side bug: either the SDK is calling the
wrong URL or using the wrong HTTP method. These must always be treated as FAIL,
even if the scenario step is otherwise conditional.

A resource-level 404 (the route exists but the specific resource is not found)
is acceptable for conditional steps and should not cause a FAIL.

See `sdk/typescript/tests/contract/contract.test.ts` (`assertNotRouteLevelError`)
for a reference implementation of this distinction.

## Per-SDK implementation guidance

### Python

The Python SDK CQRS method names differ from the REST operation names. Use the
`sdk_methods.python` values from the fixture. The Python SDK does not use HTTP;
it calls CQRS handlers in-process. The expected status codes still apply via
the handler's return value.

Suggested test file: `sdk/python/tests/parity/test_parity_scenario.py`

```python
import json, pytest
from pathlib import Path

SCENARIO = json.loads((Path(__file__).parent.parent.parent / "parity" / "scenario.json").read_text())

@pytest.mark.integration
async def test_parity_scenario(orb_client):
    for step in SCENARIO["steps"]:
        # dispatch on step["sdk_methods"]["python"], bind variables, assert shapes
        ...
```

### Go

Suggested test file: `sdk/go/orb/parity_test.go`

```go
//go:build integration

package orb_test

import (
    "encoding/json"
    "os"
    "testing"
)

func TestParityScenario(t *testing.T) {
    data, _ := os.ReadFile("../../parity/scenario.json")
    var scenario map[string]any
    json.Unmarshal(data, &scenario)
    // iterate steps, dispatch on sdk_methods["go"], bind variables, assert shapes
}
```

### TypeScript

Suggested test file: `sdk/typescript/tests/parity/parity.test.ts`

```typescript
import scenario from "../../parity/scenario.json";

describe("Parity scenario", () => {
    for (const step of scenario.steps) {
        it(`step ${step.step}: ${step.name}`, async () => {
            // dispatch on step.sdk_methods.typescript, bind variables, assert shapes
        });
    }
});
```

### Java

Suggested test file: `sdk/java/src/test/java/.../PariityScenarioTest.java`

```java
@Tag("integration")
class ParityScenarioTest {
    @Test
    void parityScenario() throws Exception {
        var scenario = parseScenarioJson(Paths.get("../../parity/scenario.json"));
        for (var step : scenario.getSteps()) {
            // dispatch on step.getSdkMethods().get("java"), bind, assert
        }
    }
}
```

### Kotlin

Suggested test file:
`sdk/kotlin/src/test/kotlin/.../ParityScenarioTest.kt`

```kotlin
@Tag("integration")
class ParityScenarioTest {
    @Test
    fun parityScenario() = runBlocking {
        val scenario = parseScenario(File("../../parity/scenario.json"))
        for (step in scenario.steps) {
            // dispatch on step.sdkMethods["kotlin"], bind, assert
        }
    }
}
```

### .NET / C#

Suggested test file: `sdk/csharp/tests/ParityScenarioTests.cs`

```csharp
[Collection("Integration")]
public class ParityScenarioTests
{
    [Fact]
    public async Task ParityScenario()
    {
        var json = await File.ReadAllTextAsync("../../parity/scenario.json");
        var scenario = JsonSerializer.Deserialize<ScenarioDoc>(json)!;
        foreach (var step in scenario.Steps)
        {
            // dispatch on step.SdkMethods["csharp"], bind, assert
        }
    }
}
```

## Running parity checks

### Static conformance (enforced today)

```bash
make sdk-spec-conformance
```

This validates that every scenario step matches a real operation in
`sdk/spec/openapi.json`. It requires no running server and is part of CI.

### Per-language runtime parity (enforced)

Each SDK exposes a `make sdk-<lang>-parity` target that spawns a real ORB over a
UDS (reusing that SDK's contract-test harness), runs the parity test file, and
reports PASS / SKIP / FAIL per step. Set `ORB_BINARY` (or `ORB_PYTHON` for .NET)
so the harness can spawn orb — the same convention the contract tests use.

```bash
make sdk-go-parity          # Go
make sdk-typescript-parity  # TypeScript
make sdk-java-parity        # Java
make sdk-kotlin-parity      # Kotlin
make sdk-csharp-parity      # .NET / C#
make sdk-parity             # all five languages
```

The parity test files:

| Language | File | Target |
|----------|------|--------|
| Go | `sdk/go/orb/parity_test.go` (`//go:build integration`) | `sdk-go-parity` |
| TypeScript | `sdk/typescript/tests/parity/parity.test.ts` | `sdk-typescript-parity` |
| Java | `sdk/java/src/test/java/.../parity/ParityScenarioTest.java` (`@Tag("parity")`) | `sdk-java-parity` |
| Kotlin | `sdk/kotlin/src/test/kotlin/.../parity/ParityScenarioTest.kt` | `sdk-kotlin-parity` |
| .NET | `sdk/csharp/tests/parity/ParityScenarioTests.cs` | `sdk-csharp-parity` |

In CI, each `sdk.yml` matrix leg runs its own parity step after the contract
tests, against the same wheel-installed orb.

## Equivalence criteria

A parity run is considered passing when:

- All steps that are not skipped complete without a route-level 404 or 405.
- Step 1 returns `status` equal to `"healthy"` or `"degraded"`.
- Step 2 returns a `templates` field that is an array (possibly empty).
- Steps 3–5 (when executed) return 2xx responses with the expected fields.
- Step 6 returns a `requests` or `data` field.
- No SDK produces a different HTTP status code than another SDK for the same
  step when all SDKs connect to the same ORB server.

## Adding new steps

To add a step to the parity scenario:

1. Add a new entry to the `steps` array in `scenario.json`, following the
   existing schema.
2. Include `operation_id` (must match a real operationId from
   `sdk/spec/openapi.json`) and `sdk_methods` entries for all six languages.
3. Update the per-SDK parity test files to handle the new step.

Do not change step numbers of existing steps — SDK tests may reference them by
number. Append new steps at the end with an incremented `step` value.
