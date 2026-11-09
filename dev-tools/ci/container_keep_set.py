#!/usr/bin/env python3
"""Decide which untagged GHCR package versions are safe to delete.

A multi-arch image is an OCI index (manifest list) whose children -- the
per-platform manifests and the BuildKit attestation manifests -- are stored as
separate package versions that carry no tag of their own. GHCR lists them as
"untagged", but the tagged index still needs them: deleting them leaves a tag
that resolves to an index whose children return 404.

This script takes the package-version listing (one JSON object per line, as
produced by ``gh api --paginate .../versions --jq '.[]'``), fetches the manifest
of every *tagged* version from the registry, and collects every digest those
manifests reference, recursing into nested indexes. Untagged versions whose
digest is in that keep-set are never reported as deletable.

The run fails closed: if any tagged manifest cannot be fetched, verified or
parsed, the script exits non-zero and writes no ``--untagged-delete-out`` file,
so the caller deletes nothing.

Only the standard library is used. The registry is read anonymously (the
package is public); no registry or package write call is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

REGISTRY = "ghcr.io"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_DEPTH = 8
HTTP_TIMEOUT_SECONDS = 30
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4

INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
ACCEPT = ", ".join(
    sorted(INDEX_MEDIA_TYPES)
    + [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)

ManifestFetcher = Callable[[str], bytes]


def _build_https_only_opener() -> urllib.request.OpenerDirector:
    """Return an opener that only speaks HTTPS (no file:// or ftp:// handlers)."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


_OPENER = _build_https_only_opener()


class KeepSetError(Exception):
    """The keep-set could not be computed reliably; nothing may be deleted."""


def _check_digest(value: object, context: str) -> str:
    if not isinstance(value, str) or not DIGEST_RE.match(value):
        raise KeepSetError(f"{context}: invalid digest {value!r}")
    return value


def _parse_manifest(raw: bytes, digest: str) -> dict:
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise KeepSetError(f"{digest}: manifest is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise KeepSetError(f"{digest}: manifest is not a JSON object")
    return doc


def collect_children(
    root_digest: str, fetch: ManifestFetcher, seen: set[str] | None = None, depth: int = 0
) -> set[str]:
    """Return every digest referenced (transitively) by the manifest ``root_digest``.

    ``fetch`` returns the raw manifest bytes for a digest and raises on any
    failure. The root itself is not part of the result.
    """
    if depth > MAX_DEPTH:
        raise KeepSetError(f"{root_digest}: index nesting deeper than {MAX_DEPTH}")
    seen = seen if seen is not None else set()
    doc = _parse_manifest(fetch(root_digest), root_digest)

    children = doc.get("manifests")
    if children is None:
        if not any(key in doc for key in ("config", "layers")):
            raise KeepSetError(f"{root_digest}: unrecognised manifest (no manifests/config/layers)")
        return set()
    if not isinstance(children, list):
        raise KeepSetError(f"{root_digest}: 'manifests' is not a list")

    found: set[str] = set()
    for entry in children:
        if not isinstance(entry, dict):
            raise KeepSetError(f"{root_digest}: manifests entry is not an object")
        child = _check_digest(entry.get("digest"), f"{root_digest} child")
        found.add(child)
        media_type = entry.get("mediaType")
        # Descend unless the child is known to be a leaf image manifest.
        if (media_type is None or media_type in INDEX_MEDIA_TYPES) and child not in seen:
            seen.add(child)
            found |= collect_children(child, fetch, seen, depth + 1)
    return found


def build_keep_set(tagged_digests: Iterable[str], fetch: ManifestFetcher) -> set[str]:
    """Union of the children of every tagged version. Raises KeepSetError on any failure."""
    keep: set[str] = set()
    for digest in sorted(set(tagged_digests)):
        _check_digest(digest, "tagged version")
        keep |= collect_children(digest, fetch, seen={digest})
    return keep


class RegistryClient:
    """Anonymous, read-only manifest fetcher for a public GHCR repository."""

    def __init__(self, repository: str, registry: str = REGISTRY, sleep=time.sleep):
        self.repository = repository
        self.registry = registry
        self._sleep = sleep
        self._token: str | None = None

    def _open(self, url: str, headers: dict[str, str]) -> bytes:
        if not url.startswith("https://"):
            raise KeepSetError(f"refusing non-https URL {url}")
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 - https-only, see _OPENER
            try:
                with _OPENER.open(request, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in RETRY_STATUSES:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            self._sleep(2**attempt)
        raise KeepSetError(f"GET {url} failed: {last}")

    def _bearer(self) -> str:
        if self._token is None:
            scope = urllib.parse.quote(f"repository:{self.repository}:pull", safe="")
            url = f"https://{self.registry}/token?service={self.registry}&scope={scope}"
            body = self._open(url, {"Accept": "application/json"})
            try:
                token = json.loads(body).get("token")
            except (ValueError, AttributeError) as exc:
                raise KeepSetError(f"registry token response unreadable: {exc}") from exc
            if not isinstance(token, str) or not token:
                raise KeepSetError("registry token response has no token")
            self._token = token
        return self._token

    def fetch(self, digest: str) -> bytes:
        _check_digest(digest, "fetch")
        url = f"https://{self.registry}/v2/{self.repository}/manifests/{digest}"
        raw = self._open(url, {"Authorization": f"Bearer {self._bearer()}", "Accept": ACCEPT})
        actual = "sha256:" + hashlib.sha256(raw).hexdigest()
        if actual != digest:
            raise KeepSetError(f"{digest}: registry returned content with digest {actual}")
        return raw


def _tags(row: dict) -> list[str]:
    return ((row.get("metadata") or {}).get("container") or {}).get("tags") or []


def _created(row: dict) -> datetime:
    return datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))


def classify_untagged(
    rows: list[dict], keep: set[str], now: datetime, min_age: timedelta
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split untagged rows into (deletable, referenced, too_recent)."""
    deletable: list[dict] = []
    referenced: list[dict] = []
    too_recent: list[dict] = []
    for row in rows:
        if row.get("name") in keep:
            referenced.append(row)
        elif now - _created(row) < min_age:
            too_recent.append(row)
        else:
            deletable.append(row)
    return deletable, referenced, too_recent


def load_versions(path: Path) -> list[dict]:
    rows: list[dict] = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise KeepSetError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if (
            not isinstance(row, dict)
            or "id" not in row
            or "name" not in row
            or "created_at" not in row
        ):
            raise KeepSetError(f"{path}:{number}: version row lacks id/name/created_at")
        rows.append(row)
    return rows


def _line(row: dict, note: str) -> str:
    return f"{row['id']} ({row['created_at']}) [untagged] [{note}] [digest: {row['name']}]\n"


def run(
    versions: Path,
    fetch: ManifestFetcher,
    *,
    keep_out: Path,
    delete_out: Path,
    kept_out: Path,
    min_age: timedelta,
    now: datetime | None = None,
) -> int:
    for out in (keep_out, delete_out, kept_out):
        out.unlink(missing_ok=True)
    now = now or datetime.now(timezone.utc)
    rows = load_versions(versions)
    tagged = [r for r in rows if _tags(r)]
    untagged = [r for r in rows if not _tags(r)]
    if untagged and not tagged:
        raise KeepSetError(
            "package has untagged versions but no tagged version; refusing to continue"
        )

    keep = build_keep_set((r["name"] for r in tagged), fetch)
    deletable, referenced, too_recent = classify_untagged(untagged, keep, now, min_age)

    keep_out.write_text("".join(f"{d}\n" for d in sorted(keep)))
    kept_out.write_text(
        "".join(_line(r, "referenced by a tagged index") for r in referenced)
        + "".join(_line(r, f"newer than {min_age}") for r in too_recent)
    )
    delete_out.write_text("".join(_line(r, "orphan") for r in deletable))
    print(
        f"tagged versions: {len(tagged)}; untagged versions: {len(untagged)}; "
        f"keep-set digests: {len(keep)}"
    )
    print(
        f"untagged kept-as-referenced: {len(referenced)}; kept-too-recent: {len(too_recent)}; "
        f"to-delete: {len(deletable)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--versions", type=Path, required=True, help="JSON-lines package versions")
    parser.add_argument(
        "--repository", required=True, help="owner/package, e.g. finos/open-resource-broker"
    )
    parser.add_argument("--keep-out", type=Path, required=True, help="file for keep-set digests")
    parser.add_argument("--untagged-delete-out", type=Path, required=True)
    parser.add_argument("--untagged-kept-out", type=Path, required=True)
    parser.add_argument("--min-age-hours", type=float, default=24.0)
    args = parser.parse_args(argv)
    client = RegistryClient(args.repository.lower())
    try:
        return run(
            args.versions,
            client.fetch,
            keep_out=args.keep_out,
            delete_out=args.untagged_delete_out,
            kept_out=args.untagged_kept_out,
            min_age=timedelta(hours=args.min_age_hours),
        )
    except KeepSetError as exc:
        for out in (args.keep_out, args.untagged_delete_out, args.untagged_kept_out):
            out.unlink(missing_ok=True)
        print(f"ERROR: aborting, nothing will be deleted: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
