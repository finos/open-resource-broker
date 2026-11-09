"""Tests for the GHCR cleanup keep-set script.

Covers:
- children and attestation manifests of a tagged index are kept
- truly orphaned untagged versions are deleted
- nested indexes are followed
- any fetch/parse failure aborts the run with no delete list written
- the registry client verifies content digests and retries transient errors
"""

import hashlib
import io
import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

_DEV_TOOLS_PATH = str(Path(__file__).parents[2] / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

import container_keep_set as cks  # type: ignore[import]

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_IMAGE = "application/vnd.oci.image.manifest.v1+json"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
OLD = "2026-10-28T10:50:38Z"
RECENT = "2026-11-09T11:00:00Z"


def d(n: int) -> str:
    return f"sha256:{n:064x}"


def image() -> bytes:
    return json.dumps({"schemaVersion": 2, "config": {}, "layers": []}).encode()


def index(*children: tuple[str, str]) -> bytes:
    return json.dumps(
        {"schemaVersion": 2, "manifests": [{"digest": dg, "mediaType": mt} for dg, mt in children]}
    ).encode()


class FakeRegistry:
    def __init__(self, manifests: dict[str, bytes]):
        self.manifests = manifests
        self.requested: list[str] = []

    def __call__(self, digest: str) -> bytes:
        self.requested.append(digest)
        if digest not in self.manifests:
            raise cks.KeepSetError(f"{digest}: 404")
        return self.manifests[digest]


def row(version_id: int, digest: str, tags: list[str] | None = None, created: str = OLD) -> dict:
    return {
        "id": version_id,
        "name": digest,
        "created_at": created,
        "metadata": {"container": {"tags": tags or []}},
    }


def write_versions(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def run_plan(tmp_path: Path, rows: list[dict], registry, min_age=timedelta(hours=24)):
    out = {k: tmp_path / f"{k}.txt" for k in ("keep", "delete", "kept")}
    status = cks.run(
        write_versions(tmp_path / "versions.jsonl", rows),
        registry,
        keep_out=out["keep"],
        delete_out=out["delete"],
        kept_out=out["kept"],
        min_age=min_age,
        now=NOW,
    )
    return status, out


@pytest.mark.unit
class TestCollectChildren:
    def test_platform_and_attestation_children_are_collected(self):
        reg = FakeRegistry({d(1): index((d(2), OCI_IMAGE), (d(3), OCI_IMAGE), (d(4), OCI_IMAGE))})
        assert cks.collect_children(d(1), reg) == {d(2), d(3), d(4)}

    def test_single_platform_image_has_no_children(self):
        assert cks.collect_children(d(1), FakeRegistry({d(1): image()})) == set()

    def test_nested_index_is_followed(self):
        reg = FakeRegistry(
            {
                d(1): index((d(2), OCI_INDEX)),
                d(2): index((d(3), OCI_IMAGE), (d(4), OCI_INDEX)),
                d(4): index((d(5), OCI_IMAGE)),
            }
        )
        assert cks.collect_children(d(1), reg) == {d(2), d(3), d(4), d(5)}

    def test_child_without_media_type_is_inspected(self):
        reg = FakeRegistry(
            {
                d(1): json.dumps({"manifests": [{"digest": d(2)}]}).encode(),
                d(2): index((d(3), OCI_IMAGE)),
            }
        )
        assert cks.collect_children(d(1), reg) == {d(2), d(3)}

    def test_cycle_terminates(self):
        reg = FakeRegistry({d(1): index((d(2), OCI_INDEX)), d(2): index((d(1), OCI_INDEX))})
        assert cks.build_keep_set([d(1)], reg) == {d(1), d(2)}

    def test_excessive_nesting_aborts(self):
        manifests = {d(i): index((d(i + 1), OCI_INDEX)) for i in range(1, 20)}
        with pytest.raises(cks.KeepSetError, match="nesting"):
            cks.collect_children(d(1), FakeRegistry(manifests))

    @pytest.mark.parametrize(
        "raw",
        [
            b"not json",
            b"[]",
            b'{"schemaVersion": 2}',
            b'{"manifests": "x"}',
            b'{"manifests": ["x"]}',
            b'{"manifests": [{"digest": "sha256:short"}]}',
            b'{"manifests": [{}]}',
        ],
    )
    def test_unparseable_manifest_raises(self, raw):
        with pytest.raises(cks.KeepSetError):
            cks.collect_children(d(1), FakeRegistry({d(1): raw}))

    def test_nested_fetch_failure_raises(self):
        reg = FakeRegistry({d(1): index((d(2), OCI_INDEX))})
        with pytest.raises(cks.KeepSetError, match="404"):
            cks.collect_children(d(1), reg)


@pytest.mark.unit
class TestRun:
    def rows(self):
        return [
            row(1, d(1), ["1.9.2", "python3.14"]),
            row(2, d(2)),  # amd64 child
            row(3, d(3)),  # arm64 child
            row(4, d(4)),  # attestation child
            row(5, d(9)),  # orphan
        ]

    def registry(self):
        return FakeRegistry({d(1): index((d(2), OCI_IMAGE), (d(3), OCI_IMAGE), (d(4), OCI_IMAGE))})

    def test_children_kept_and_orphan_deleted(self, tmp_path, capsys):
        status, out = run_plan(tmp_path, self.rows(), self.registry())
        assert status == 0
        deletions = out["delete"].read_text().splitlines()
        assert len(deletions) == 1 and deletions[0].startswith("5 ")
        kept = out["kept"].read_text()
        for version_id in ("2", "3", "4"):
            assert f"\n{version_id} " in "\n" + kept
        assert set(out["keep"].read_text().split()) == {d(2), d(3), d(4)}
        printed = capsys.readouterr().out
        assert "kept-as-referenced: 3" in printed and "to-delete: 1" in printed

    def test_attestation_only_reference_is_kept(self, tmp_path):
        rows = [row(1, d(1), ["v1"]), row(2, d(2))]
        status, out = run_plan(tmp_path, rows, FakeRegistry({d(1): index((d(2), OCI_IMAGE))}))
        assert status == 0 and out["delete"].read_text() == ""

    def test_nested_children_kept(self, tmp_path):
        reg = FakeRegistry({d(1): index((d(2), OCI_INDEX)), d(2): index((d(3), OCI_IMAGE))})
        rows = [row(1, d(1), ["v1"]), row(2, d(2)), row(3, d(3)), row(4, d(4))]
        _, out = run_plan(tmp_path, rows, reg)
        assert [line.split()[0] for line in out["delete"].read_text().splitlines()] == ["4"]

    def test_recent_orphan_is_kept(self, tmp_path):
        rows = [row(1, d(1), ["v1"]), row(2, d(7), created=RECENT), row(3, d(8))]
        _, out = run_plan(tmp_path, rows, FakeRegistry({d(1): image()}))
        assert [line.split()[0] for line in out["delete"].read_text().splitlines()] == ["3"]
        assert "2 (" in out["kept"].read_text()

    def test_tagged_versions_are_never_listed_for_deletion(self, tmp_path):
        rows = [row(1, d(1), ["v1"]), row(2, d(2), ["main"])]
        _, out = run_plan(tmp_path, rows, FakeRegistry({d(1): image(), d(2): image()}))
        assert out["delete"].read_text() == ""

    def test_fetch_failure_aborts_with_zero_deletions(self, tmp_path):
        rows = self.rows() + [row(6, d(6), ["v1.9.1"])]  # tagged index d(6) is missing
        with pytest.raises(cks.KeepSetError):
            run_plan(tmp_path, rows, self.registry())
        assert not (tmp_path / "delete.txt").exists()

    def test_stale_outputs_are_removed_on_failure(self, tmp_path):
        (tmp_path / "delete.txt").write_text("5 (x) [untagged]\n")
        with pytest.raises(cks.KeepSetError):
            run_plan(tmp_path, self.rows(), FakeRegistry({}))
        assert not (tmp_path / "delete.txt").exists()

    def test_untagged_without_any_tagged_version_aborts(self, tmp_path):
        with pytest.raises(cks.KeepSetError, match="no tagged"):
            run_plan(tmp_path, [row(1, d(1))], FakeRegistry({}))

    def test_malformed_version_row_aborts(self, tmp_path):
        path = tmp_path / "versions.jsonl"
        path.write_text('{"id": 1}\n')
        with pytest.raises(cks.KeepSetError, match="lacks"):
            cks.load_versions(path)


@pytest.mark.unit
class TestMain:
    def test_main_returns_1_and_deletes_nothing_on_failure(self, tmp_path, capsys):
        versions = write_versions(tmp_path / "v.jsonl", [row(1, d(1), ["v1"]), row(2, d(2))])
        delete = tmp_path / "delete.txt"
        delete.write_text("stale\n")
        argv = [
            "--versions", str(versions),
            "--repository", "finos/open-resource-broker",
            "--keep-out", str(tmp_path / "keep.txt"),
            "--untagged-delete-out", str(delete),
            "--untagged-kept-out", str(tmp_path / "kept.txt"),
        ]  # fmt: skip
        with patch.object(cks.RegistryClient, "fetch", side_effect=cks.KeepSetError("boom")):
            assert cks.main(argv) == 1
        assert not delete.exists()
        assert "nothing will be deleted" in capsys.readouterr().err


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.unit
class TestRegistryClient:
    def client(self):
        return cks.RegistryClient("finos/open-resource-broker", sleep=lambda _s: None)

    def test_fetch_uses_bearer_and_verifies_digest(self):
        body = index((d(2), OCI_IMAGE))
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        seen = []

        def fake_urlopen(request, timeout):
            seen.append(request)
            if "/token" in request.full_url:
                return FakeResponse(b'{"token": "abc"}')
            return FakeResponse(body)

        with patch("urllib.request.urlopen", fake_urlopen):
            assert self.client().fetch(digest) == body
        manifest_request = seen[-1]
        assert manifest_request.get_header("Authorization") == "Bearer abc"
        assert "manifest.list" in manifest_request.get_header("Accept")
        assert manifest_request.full_url.endswith(
            f"/v2/finos/open-resource-broker/manifests/{digest}"
        )

    def test_digest_mismatch_aborts(self):
        def fake_urlopen(request, timeout):
            return FakeResponse(b'{"token": "abc"}' if "/token" in request.full_url else b"{}")

        with (
            patch("urllib.request.urlopen", fake_urlopen),
            pytest.raises(cks.KeepSetError, match="content with digest"),
        ):
            self.client().fetch(d(1))

    def test_404_aborts_without_retry(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if "/token" in request.full_url:
                return FakeResponse(b'{"token": "abc"}')
            raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, None)  # type: ignore[arg-type]

        with (
            patch("urllib.request.urlopen", fake_urlopen),
            pytest.raises(cks.KeepSetError, match="404"),
        ):
            self.client().fetch(d(1))
        assert len(calls) == 2

    def test_transient_error_is_retried(self):
        body = image()
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        state = {"n": 0}

        def fake_urlopen(request, timeout):
            if "/token" in request.full_url:
                return FakeResponse(b'{"token": "abc"}')
            state["n"] += 1
            if state["n"] < 3:
                raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)  # type: ignore[arg-type]
            return FakeResponse(body)

        with patch("urllib.request.urlopen", fake_urlopen):
            assert self.client().fetch(digest) == body

    def test_missing_token_aborts(self):
        with (
            patch("urllib.request.urlopen", lambda r, timeout: FakeResponse(b"{}")),
            pytest.raises(cks.KeepSetError, match="no token"),
        ):
            self.client().fetch(d(1))

    def test_rejects_invalid_digest_before_any_request(self):
        with patch("urllib.request.urlopen") as urlopen, pytest.raises(cks.KeepSetError):
            self.client().fetch("sha256:../../x")
        urlopen.assert_not_called()
