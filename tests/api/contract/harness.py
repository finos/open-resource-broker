"""Helpers for the REST error contract suite.

The suite drives the real application object in-process. Routes are discovered from
the application itself, request parameters are derived from its OpenAPI document,
and faults are injected through FastAPI dependency overrides at the points where
routes reach the application layer.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

import orb.api.dependencies as deps

SECRET = "contract-suite-secret-key-0123456789abcdef"


# ---------------------------------------------------------------------------
# Route discovery
# ---------------------------------------------------------------------------


def iter_api_routes(routes: Any, prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """Yield (full path, route) for every API route, descending into included routers."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            yield from iter_api_routes(
                route.original_router.routes, prefix + route.include_context.prefix
            )


def _openapi_path(path: str) -> str:
    """Drop path converters (``{key:path}``) the way the OpenAPI document does."""
    return re.sub(r"\{(\w+):\w+\}", r"{\1}", path)


def route_keys(app: FastAPI) -> dict[str, APIRoute]:
    """Map ``"METHOD /path"`` to the route serving it."""
    keys: dict[str, APIRoute] = {}
    for path, route in iter_api_routes(app.routes):
        for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            keys[f"{method} {_openapi_path(path)}"] = route
    return keys


def _dependency_calls(dependant: Any) -> Iterator[Callable[..., Any]]:
    for sub in dependant.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from _dependency_calls(sub)


# Providers in ``orb.api.dependencies`` through which routes reach the application
# layer. Overriding one of these makes the route see the injected fault.
INJECTABLE: dict[str, Callable[..., Any]] = {
    name: func
    for name, func in inspect.getmembers(deps, inspect.isfunction)
    if (name.startswith("get_") and name.endswith("_orchestrator"))
    or name
    in {
        "get_config_manager",
        "get_health_check_port",
        "get_template_generation_service",
        "get_query_bus",
        "get_command_bus",
    }
}


def seams_for(route: APIRoute) -> list[str]:
    """Names of the injectable providers the route depends on."""
    used = {c for c in _dependency_calls(route.dependant) if c in INJECTABLE.values()}
    return sorted(name for name, func in INJECTABLE.items() if func in used)


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------


class Fault:
    """Stand-in for an application-layer object whose every call raises ``exc``."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("__"):
            raise AttributeError(name)

        def _raise(*_args: Any, **_kwargs: Any) -> Any:
            raise self._exc

        return _raise


def inject(app: FastAPI, seams: list[str], exc: Exception) -> None:
    # The override must take no parameters, or FastAPI would treat them as request inputs.
    fault = Fault(exc)
    for name in seams:
        app.dependency_overrides[INJECTABLE[name]] = lambda: fault


def clear(app: FastAPI) -> None:
    for func in INJECTABLE.values():
        app.dependency_overrides.pop(func, None)


# ---------------------------------------------------------------------------
# Probes derived from the OpenAPI document
# ---------------------------------------------------------------------------


def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        node: Any = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def sample(schema: dict[str, Any], spec: dict[str, Any], depth: int = 0) -> Any:
    """Smallest value satisfying a JSON schema (enough for request bodies here)."""
    schema = _resolve(schema, spec)
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if _resolve(o, spec).get("type") != "null"]
            return sample(options[0], spec, depth + 1) if options else None
    if "allOf" in schema:
        return sample(schema["allOf"][0], spec, depth + 1)
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        if depth > 4:
            return {}
        required = schema.get("required", [])
        props = schema.get("properties", {})
        return {k: sample(props[k], spec, depth + 1) for k in required if k in props}
    if kind == "array":
        return []
    if kind == "integer":
        return max(int(schema.get("minimum") or 1), 1)
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    return "x"


@dataclass
class Probe:
    method: str
    url: str
    json: Any = None
    params: dict[str, Any] = field(default_factory=dict)


def build_probe(key: str, spec: dict[str, Any]) -> Probe:
    """Build a request for ``"METHOD /path"`` with unknown ids in every path parameter."""
    method, path = key.split(" ", 1)
    operation = spec["paths"][path][method.lower()]
    url = path
    params: dict[str, Any] = {}
    for param in operation.get("parameters", []):
        resolved = _resolve(param, spec)
        if resolved["in"] == "path":
            value = f"unknown-{resolved['name']}"
            url = url.replace("{" + resolved["name"] + "}", value)
        elif resolved["in"] == "query" and resolved.get("required"):
            params[resolved["name"]] = sample(resolved.get("schema", {}), spec)
    # ``{key:path}`` style converters are not in the OpenAPI path.
    body = None
    request_body = operation.get("requestBody")
    if request_body:
        content = _resolve(request_body, spec).get("content", {})
        media = content.get("application/json")
        if media is not None:
            body = sample(media.get("schema", {}), spec)
    return Probe(method=method, url=url, json=body, params=params)
