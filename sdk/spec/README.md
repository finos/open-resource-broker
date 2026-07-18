# sdk/spec — Language-Neutral API Contract

This directory holds the canonical OpenAPI specification for the Open Resource Broker API.

## What lives here

| File | Description |
|------|-------------|
| `openapi.json` | Generated OpenAPI 3.x spec exported from a running ORB server |

## How it is generated

`openapi.json` is **not hand-edited** — it is generated at release time by starting a real ORB
server and fetching the `/openapi.json` endpoint:

```bash
make sdk-go-export-spec          # or: ./dev-tools/release/export_openapi_spec.sh
```

The exported spec is committed alongside each release tag so every SDK generator
has a stable, versioned source of truth.

## Why language-neutral

Previously the spec lived at `sdk/go/openapi.json`, which implied it was Go-only.
It was relocated here so that future SDKs (Python, TypeScript, Java, …) can all
consume the same single spec without duplicating or diverging it.

## Consumers

| SDK | Generator | Config |
|-----|-----------|--------|
| `sdk/go` | [openapi-generator](https://openapi-generator.tech) (`-g go`, v7.23.0) | `sdk/go/openapi-generator-config.yaml` |

Additional SDK generators should be added as subdirectories of `sdk/` with their
own tooling config pointing at `../spec/openapi.json`.
