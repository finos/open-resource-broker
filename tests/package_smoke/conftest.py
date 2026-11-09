"""Fixtures for the process-level smoke on a built wheel.

The smoke installs the wheel into a fresh virtual environment (never editable,
never the development environment), points every ``orb`` subprocess at a moto
server through ``AWS_ENDPOINT_URL`` and drives the installed package as a user
would: CLI, server, UI websocket.

Run it by path; ``tests/package_smoke`` is excluded from directory collection::

    ORB_SMOKE_WHEEL=dist/orb_py-<version>-py3-none-any.whl \\
        uv run pytest tests/package_smoke -p no:xdist

``ORB_SMOKE_MOTO_SERVER`` may name the ``moto_server`` executable; it defaults
to the one next to the interpreter running pytest. Without a wheel the module
skips, unless ``ORB_SMOKE_REQUIRED=1`` is set, which turns missing inputs into
failures.

Everything the smoke creates lives under one temporary directory. Processes it
starts are stopped by the PID it recorded when starting them, never by name.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.shared.moto_network import seed_moto_network

SMOKE_REGION = "eu-west-2"
SMOKE_PROFILE = "orb-smoke"
REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS = Path(__file__).parent / "helpers"
REQUIRED = os.environ.get("ORB_SMOKE_REQUIRED") == "1"

# Template used for the acquire and return flow. RunInstances status needs
# DescribeInstances to filter on reservation-id, which moto's server mode does
# not implement, so a RunInstances request would never be seen to start. An
# instant fleet reports its instances synchronously.
FLOW_TEMPLATE = "EC2Fleet-Instant-OnDemand"
SPOT_TEMPLATE = "RunInstances-Spot"
ON_DEMAND_TEMPLATE = "RunInstances-OnDemand"


def _missing(what: str) -> None:
    message = f"{what} is not set; the package smoke needs a built wheel"
    if REQUIRED:
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def json_from(text: str) -> Any:
    """The first JSON document in ``text``; the CLI may print text around it."""
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    assert start >= 0, f"no JSON in output:\n{text}"
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    return value


def wait_for_http(url: str, *, timeout: float, alive: Any = None) -> int:
    """Poll ``url`` until it answers; return the status. Fails on timeout."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        if alive is not None and alive() is not None:
            pytest.fail(f"the process serving {url} exited with {alive()} before it answered")
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - loopback
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except OSError as exc:
            last = str(exc)
        time.sleep(0.5)
    raise AssertionError(f"{url} did not answer within {timeout:.0f}s ({last})")


@dataclass
class Venv:
    root: Path
    python: Path

    @property
    def bin(self) -> Path:
        return self.python.parent

    def site_packages(self) -> Path:
        out = subprocess.run(
            [str(self.python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return Path(out.stdout.strip())


@dataclass
class Smoke:
    """Everything one smoke run shares: directories, venv, moto and a command runner."""

    root: Path
    wheel: Path
    venv: Venv
    moto_url: str
    subnet_id: str
    security_group_id: str
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def helpers(self) -> Path:
        return HELPERS

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    def env(self, **extra: str) -> dict[str, str]:
        """The only environment an ``orb`` subprocess gets.

        Nothing is inherited: no AWS profile or keys, no BRIDGE_* or REFLEX_*
        variables, no home directory of the caller. The AWS identity is a pair
        of temporary files holding dummy credentials, so a request that
        bypassed the moto endpoint would fail on authentication.
        """
        env = {
            "PATH": f"{self.venv.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.root / "home"),
            "TMPDIR": str(self.root / "tmp"),
            "ORB_CONFIG_DIR": str(self.config_dir),
            "ORB_WORK_DIR": str(self.work),
            "AWS_ENDPOINT_URL": self.moto_url,
            "AWS_CONFIG_FILE": str(self.root / "aws-config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(self.root / "aws-credentials"),
            "AWS_DEFAULT_REGION": SMOKE_REGION,
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
        }
        env.update(self.extra_env)
        env.update(extra)
        return env

    def run(
        self,
        *args: str,
        timeout: float = 120,
        env: dict[str, str] | None = None,
        ok: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``<venv python> -m orb <args>``; on failure show everything it printed."""
        command = [str(self.venv.python), "-m", "orb", *args]
        return self._run(command, timeout=timeout, env=env, ok=ok)

    def run_python(
        self, *args: str, timeout: float = 120, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the venv interpreter in isolated mode (``-I``)."""
        return self._run([str(self.venv.python), "-I", *args], timeout=timeout, env=env, ok=True)

    def _run(
        self, command: list[str], *, timeout: float, env: dict[str, str] | None, ok: bool
    ) -> subprocess.CompletedProcess[str]:
        try:
            done = subprocess.run(
                command,
                cwd=self.work,
                env=env if env is not None else self.env(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(
                f"{' '.join(command[1:])} timed out after {timeout:.0f}s\n"
                f"stdout:\n{_text(exc.stdout)}\nstderr:\n{_text(exc.stderr)}",
                pytrace=False,
            )
        if ok and done.returncode != 0:
            pytest.fail(
                f"{' '.join(command[1:])} exited {done.returncode}\n"
                f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}",
                pytrace=False,
            )
        return done

    def ec2(self) -> Any:
        """A boto3 client against the moto server, from the test process."""
        import boto3

        return boto3.client(
            "ec2",
            region_name=SMOKE_REGION,
            endpoint_url=self.moto_url,
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _scrub(env: dict[str, str]) -> dict[str, str]:
    """The caller's environment minus anything that points at real cloud or at Bridge."""
    drop = ("AWS_", "BRIDGE_", "REFLEX_", "ORB_")
    return {k: v for k, v in env.items() if not k.startswith(drop)}


@pytest.fixture(scope="session")
def smoke_root(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("package-smoke")
    for name in ("home", "tmp", "work", "config"):
        (root / name).mkdir()
    (root / "aws-config").write_text(f"[profile {SMOKE_PROFILE}]\nregion = {SMOKE_REGION}\n")
    (root / "aws-credentials").write_text(
        f"[{SMOKE_PROFILE}]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def smoke_wheel() -> Path:
    value = os.environ.get("ORB_SMOKE_WHEEL")
    if not value:
        _missing("ORB_SMOKE_WHEEL")
    wheel = Path(value).resolve()
    assert wheel.is_file(), f"ORB_SMOKE_WHEEL names {wheel}, which is not a file"
    return wheel


@pytest.fixture(scope="session")
def moto_server_path() -> str:
    value = os.environ.get("ORB_SMOKE_MOTO_SERVER") or shutil.which(
        "moto_server", path=str(Path(sys.executable).parent)
    )
    if not value:
        _missing("ORB_SMOKE_MOTO_SERVER (or a moto_server next to the test interpreter)")
    assert value is not None
    return value


@pytest.fixture(scope="session")
def smoke_venv(smoke_root: Path, smoke_wheel: Path) -> Venv:
    """A fresh virtual environment with the wheel and its extras, pinned by uv.lock."""
    base_env = _scrub(dict(os.environ))
    constraints = smoke_root / "constraints.txt"
    exported = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-hashes",
            "--no-dev",
            "--extra",
            "all",
            "--no-emit-project",
            "--no-header",
        ],
        cwd=REPO_ROOT,
        env=base_env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert exported.returncode == 0, f"uv export failed:\n{exported.stderr}"
    constraints.write_text(exported.stdout)

    root = smoke_root / "venv"
    python = root / "bin" / "python"
    for command in (
        ["uv", "venv", "--quiet", "--python", sys.executable, str(root)],
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            f"{smoke_wheel}[all]",
        ],
    ):
        done = subprocess.run(
            command, env=base_env, capture_output=True, text=True, timeout=900, check=False
        )
        assert done.returncode == 0, f"{' '.join(command[:3])} failed:\n{done.stderr}"
    return Venv(root=root, python=python)


@pytest.fixture(scope="session")
def moto_endpoint(smoke_root: Path, moto_server_path: str) -> Iterator[dict[str, str]]:
    """A moto server on a loopback port with the network the provider needs."""
    port = free_port()
    log = (smoke_root / "moto.log").open("w")
    process = subprocess.Popen(  # noqa: S603 - executable path comes from the test setup
        [moto_server_path, "-H", "127.0.0.1", "-p", str(port)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=_scrub(dict(os.environ)),
    )
    pid = process.pid
    try:
        url = f"http://127.0.0.1:{port}"
        wait_for_http(f"{url}/moto-api/", timeout=60, alive=process.poll)
        import boto3

        ec2 = boto3.client(
            "ec2",
            region_name=SMOKE_REGION,
            endpoint_url=url,
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        network = seed_moto_network(ec2, SMOKE_REGION)
        yield {"url": url, "subnet_id": network["subnet_ids"][0], "sg_id": network["sg_id"]}
    finally:
        _stop(process, pid)
        log.close()


def _stop(process: subprocess.Popen[Any], pid: int) -> None:
    """Stop exactly the process this fixture started."""
    if process.poll() is not None:
        return
    os.kill(pid, signal.SIGTERM)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.kill(pid, signal.SIGKILL)
        process.wait(timeout=10)


@pytest.fixture(scope="session")
def smoke(smoke_root: Path, smoke_wheel: Path, smoke_venv: Venv, moto_endpoint: dict[str, str]):
    return Smoke(
        root=smoke_root,
        wheel=smoke_wheel,
        venv=smoke_venv,
        moto_url=moto_endpoint["url"],
        subnet_id=moto_endpoint["subnet_id"],
        security_group_id=moto_endpoint["sg_id"],
    )


@pytest.fixture(scope="session")
def smoke_config(smoke: Smoke) -> dict[str, Any]:
    """``orb init`` against moto, templates generated, RunInstances spot switched off.

    The override is merged over the shipped handler defaults, so the spot
    template is rejected whatever those defaults say.
    """
    smoke.run(
        "init",
        "--non-interactive",
        "--provider-type",
        "aws",
        "--aws-profile",
        SMOKE_PROFILE,
        "--aws-region",
        SMOKE_REGION,
        "--subnet-ids",
        smoke.subnet_id,
        "--security-group-ids",
        smoke.security_group_id,
        "--config-dir",
        str(smoke.config_dir),
    )
    path = smoke.config_dir / "config.json"
    config = json.loads(path.read_text())
    providers = config["provider"]["providers"]
    assert len(providers) == 1, providers
    providers[0]["handler_overrides"] = {
        "RunInstances": {"handler_class": "RunInstancesHandler", "supports_spot": False}
    }
    path.write_text(json.dumps(config, indent=2))
    smoke.run("templates", "generate")
    return config


@dataclass
class Flow:
    """What the CLI acquire and return flow produced."""

    templates: list[dict[str, Any]]
    request_id: str
    machine_id: str
    return_request_id: str


@pytest.fixture(scope="session")
def cli_flow(smoke: Smoke, smoke_config: dict[str, Any]) -> Flow:
    """Acquire one machine, wait for it, return it, wait for that."""
    listed = json_from(smoke.run("templates", "list", "--format", "json").stdout)
    templates = listed["templates"]
    assert templates, f"templates list is empty: {listed}"

    created = json_from(
        smoke.run("machines", "request", FLOW_TEMPLATE, "1", "--format", "json").stdout
    )
    request_id = created["request_id"]

    status = _status(smoke, request_id)
    machines = status["machines"]
    machine_id = machines[0]["machine_id"] if machines else ""
    if status["status"] != "complete" or len(machines) != 1 or machines[0]["status"] != "running":
        pytest.fail(f"acquire request did not complete with one running machine:\n{status}")

    returned = json_from(
        smoke.run(
            "machines",
            "return",
            machine_id,
            "--wait",
            "--timeout",
            "60",
            "--format",
            "json",
            timeout=90,
        ).stdout
    )
    return_request_id = returned["request_id"]
    done = _status(smoke, return_request_id)
    if done["status"] != "complete":
        pytest.fail(f"return request did not complete:\n{done}")
    return Flow(templates, request_id, machine_id, return_request_id)


def _status(smoke: Smoke, request_id: str) -> dict[str, Any]:
    out = smoke.run(
        "requests", "status", request_id, "--wait", "--timeout", "60", "--format", "json",
        timeout=90,
    ).stdout  # fmt: skip
    return json_from(out)["requests"][0]


@dataclass
class Server:
    base: str
    port: int
    log: Path
    errors: Path
    token: str

    @property
    def api(self) -> str:
        return f"{self.base}/orb"

    def log_text(self) -> str:
        return self.log.read_text(errors="replace") + self.errors.read_text(errors="replace")


@pytest.fixture(scope="session")
def server(smoke: Smoke, cli_flow: Flow) -> Iterator[Server]:
    """``orb server start --foreground`` on a free loopback port, default state manager.

    Foreground, so the PID recorded here is the server's own and stopping it
    stops the UI backend it supervises. The PID file, the loopback token and the
    logs all stay under the smoke directory.
    """
    port = free_port()
    out = (smoke.root / "server.out").open("w")
    err = (smoke.root / "server.err").open("w")
    process = subprocess.Popen(  # noqa: S603 - the venv interpreter created by this run
        [
            str(smoke.venv.python),
            "-m",
            "orb",
            "server",
            "start",
            "--foreground",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=smoke.work,
        env=smoke.env(ORB_BASE_URL=f"http://127.0.0.1:{port}"),
        stdout=out,
        stderr=err,
    )
    pid = process.pid
    try:
        base = f"http://127.0.0.1:{port}"
        status = wait_for_http(f"{base}/orb/health", timeout=180, alive=process.poll)
        assert status == 200, f"/orb/health answered {status}"
        token_file = smoke.work / "server" / "orb-server.token"
        token = token_file.read_text().strip() if token_file.is_file() else ""
        yield Server(base, port, smoke.root / "server.out", smoke.root / "server.err", token)
    finally:
        _stop(process, pid)
        out.close()
        err.close()


URL_LINE = re.compile(r"ORB API listening on (http://127\.0\.0\.1:\d+\S*)")
