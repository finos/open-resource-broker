"""Guards for .pre-commit-config.yaml hooks and the OpenAPI export script."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _hooks():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    return {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}


def test_export_openapi_spec_hook_runs_on_pre_push():
    hook = _hooks()["export-openapi-spec"]
    assert hook["stages"] == ["pre-push"]
    assert hook["entry"] == "make sdk-go-export-spec"
    assert hook["pass_filenames"] is False
    pattern = re.compile(hook["files"])
    assert pattern.search("src/orb/api/routers/requests.py")
    assert pattern.search("src/orb/application/dto/x.py")
    assert pattern.search("dev-tools/release/export_openapi_spec.sh")
    assert not pattern.search("README.md")


def test_export_target_exists():
    makefiles = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "makefiles").glob("*.mk"))
    assert re.search(r"^sdk-go-export-spec:", makefiles, re.MULTILINE)


def test_export_script_socket_is_private_to_each_run():
    script = (ROOT / "dev-tools" / "release" / "export_openapi_spec.sh").read_text(encoding="utf-8")
    assignments = re.findall(r"^SOCK=(.*)$", script, re.MULTILINE)
    assert assignments
    assert not any("/tmp/" in value for value in assignments)


def test_file_size_hooks_are_not_described_as_warning_only():
    hooks = _hooks()
    for hook_id in ("check-file-sizes", "check-file-sizes-arch"):
        text = (hooks[hook_id]["description"] + hooks[hook_id]["name"]).lower()
        assert "warning" not in text
    raw = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert (
        "run as warning only" not in raw.split("id: check-file-sizes-arch", 1)[1].split("\n\n")[0]
    )
