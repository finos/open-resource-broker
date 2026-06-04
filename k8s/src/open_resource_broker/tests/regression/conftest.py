"""Morgan Stanley makes this available to you under the Apache License,
Version 2.0 (the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file
distributed with this work for additional information regarding
copyright ownership. Unless required by applicable law or agreed
to in writing, software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
See the License for the specific language governing permissions and
limitations under the License. Watch and manage open-resource-broker machine
requests and pods in a Kubernetes cluster.

Test configuration for regression testing
"""

from __future__ import annotations

import logging
import pathlib
import random
import sqlite3
import tempfile
import threading
from functools import partial
from time import sleep

import click.testing
import pytest
import yaml

from open_resource_broker.cli.hf import run as hf
from open_resource_broker.cli.hfadmin import run as hfadmin
from open_resource_broker.impl.hfadmin import delete_pods_in_namespace
from open_resource_broker.impl.hfadmin import drain_node_in_namespace
from open_resource_broker.impl.hfadmin import get_pods_in_current_namespace
from open_resource_broker.tests import generate_provider_conf
from open_resource_broker.tests import get_workdir

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def pytest_collect_file(parent, file_path):
    """Collects the yaml test files"""
    if file_path.suffix == ".yaml" and file_path.name.startswith("test"):
        return YamlFile.from_parent(parent, path=file_path)

    return None


class YamlFile(pytest.File):
    """A test group to run from a yaml file."""

    def collect(self):  # noqa: D102
        yaml_tests = yaml.safe_load(self.path.open(encoding="utf-8"))
        logger.info("Raw spec is %s", yaml_tests)
        delete_pods_in_namespace()
        for test_case in yaml_tests:
            test_function = pytest.Function.from_parent(
                name=test_case["name"],
                parent=self,
                callobj=partial(run_custom_hostfactory_test, test_case),
            )
            test_function.add_marker(pytest.mark.regression)
            yield test_function

    # TODO Clean up after ourselves


def _run_cli(module: str, args: list) -> click.testing.Result:
    runner = click.testing.CliRunner()
    logger.info("Running %s with args %s.", module, args)

    result = runner.invoke(
        module,
        args,
        catch_exceptions=False,
        standalone_mode=False,
    )

    logger.info("Result of %s %s is %s", str(module), " ".join(args), result)

    return result


def run_hostfactory_command(
    command: str, json_in: str, confdir: str | None = None
) -> click.testing.Result:
    """Run a open-resource-broker command"""
    logger.info("Json in is %s", json_in)

    with tempfile.NamedTemporaryFile(
        delete=False, mode="w", encoding="utf-8"
    ) as json_file:
        json_file.write(json_in)
        json_file.flush()
        logger.debug("Json is written to %s", json_file.name)
        hf_args = ["--workdir", get_workdir(), command, str(json_file.name)]
        if confdir:
            hf_args = ["--confdir", confdir, *hf_args]

        result = _run_cli(
            hf,
            hf_args,
        )

        logger.info("Returned open-resource-broker command.")
        assert result.exit_code == 0, result.output
        return result


def run_hostfactory_admin_command(command: str) -> click.testing.Result:
    """Run a open-resource-broker admin command"""
    result = _run_cli(hfadmin, ["--workdir", get_workdir(), *command.split(" ")])

    assert result.exit_code == 0
    assert result.output is not None, result.output
    logger.info("Hostfactory admin output is %s", result.output)

    return result


def run_pod_watch_command() -> click.testing.Result:
    """Run pod watcher command"""
    return _run_cli(hf, ["--workdir", get_workdir(), "watch", "pods"])


def run_request_machine_command() -> click.testing.Result:
    """Run request-machines watcher command"""
    return _run_cli(
        hf,
        [
            "--workdir",
            get_workdir(),
            "watch",
            "request-machines",
        ],
    )


def run_request_return_command() -> click.testing.Result:
    """Run request-return-machines watcher command"""
    return _run_cli(
        hf,
        ["--workdir", get_workdir(), "watch", "request-return-machines"],
    )


def run_event_command() -> click.testing.Result:
    """Run a pod watch command"""
    return _run_cli(
        hf,
        ["--workdir", get_workdir(), "watch", "events", "--no-rotate-events"],
    )


def run_custom_hostfactory_test(  # noqa: C901, PLR0912
    test_spec: dict,
    flavor: str,
    run_hostfactory_pods,  # noqa: ARG001
    run_hostfactory_machines,  # noqa: ARG001
    run_hostfactory_events,  # noqa: ARG001
    run_hostfactory_returns,  # noqa: ARG001
) -> None:
    """Run a custom open-resource-broker test."""
    logger.info("Test spec is %s", test_spec)

    if "open-resource-broker-admin" in test_spec:
        args = ""
        if "request-return-machines" in test_spec["open-resource-broker-admin"]:
            # TODO consider do a return all piece of logic
            logger.info("Populating with the list of machines")
            machines = run_hostfactory_admin_command("list-machines").output.split()
            if "return_count" in test_spec:
                machines = random.sample(machines, test_spec["return_count"])
            args = " " + " ".join(machines)
        cmd = test_spec["open-resource-broker-admin"] + args
        result = run_hostfactory_admin_command(cmd)
        json_in = result.output
        assert json_in is not None, result

    if "open-resource-broker" in test_spec:
        logger.info(
            "Json in is %s and open-resource-broker command is %s",
            json_in,
            test_spec["open-resource-broker"],
        )

        confdir = None
        if "request-machines" in test_spec["open-resource-broker"]:
            confdir = generate_provider_conf(flavor)

        run_hostfactory_command(test_spec["open-resource-broker"], json_in, confdir)

    if "list-machines" in test_spec:
        limit = 10
        iteration = 0
        while (
            not run_hostfactory_admin_command("list-machines").output
            < test_spec["list-machines"]
            and iteration < limit
        ):
            sleep(10)
            logger.info("Waiting for pods to be reach expected count")
            iteration += 1
        if iteration >= limit:
            raise AssertionError("Pods did not reach expected count")
        if "timings" in test_spec["target"]:
            verify_timings(test_spec["target"]["timings"])

    if "drain_node" in test_spec:
        logger.info("Draining node")
        value = test_spec["drain_node"]
        for _ in range(value):
            deleted_pods = drain_node_in_namespace()
            test_spec["target"]["pods"] -= deleted_pods
            logger.info(
                "We have forcibly stopped %s nodes, the target node count is %s",
                deleted_pods,
                test_spec["target"]["pods"],
            )

    if "target" in test_spec:
        logger.info("Target is %s", test_spec["target"])
        limit = 10
        iteration = 0
        while not matches_pod_count(test_spec["target"]["pods"]) and iteration < limit:
            sleep(10)
            logger.info("Waiting for pods to be reach expected count")
            iteration += 1
        if iteration >= limit:
            raise AssertionError("Pods did not reach expected count")
        if "timings" in test_spec["target"]:
            verify_timings(test_spec["target"]["timings"])


def matches_pod_count(expected_pod_count: int) -> bool:
    """Check if the pod count matches the expected count."""
    current_pods = get_pods_in_current_namespace()
    current_pod_count = len(current_pods.items)
    logger.info("Current pod count is %s", current_pod_count)
    return current_pod_count == expected_pod_count


def find_event_average(workdir, event_from, event_to):
    """Calculate average time between two type of events"""
    dbfile = pathlib.Path(workdir) / "events.db"
    conn = None
    try:
        conn = sqlite3.connect(dbfile)
        sql = f"""
        select avg(t2.timestamp - t1.timestamp) from
            (select id, min(timestamp) as timestamp from
                events where category='pod' and type='status' and value='{event_from}'
                    group by id) as t1
            join
            (select id, min(timestamp) as timestamp from
                events where category='pod' and type='status' and value='{event_to}'
                    group by id) as t2
            on t1.id = t2.id
        """  # noqa: S608
        return conn.execute(sql).fetchone()[0]
    finally:
        if conn:
            conn.close()


def verify_timings(expected_timings: dict) -> None:
    """Verifies the timings of the requests."""
    logger.info("Expected_timings are %s", expected_timings)
    for expected_timing in expected_timings:
        from_event = expected_timing["from"]
        to_event = expected_timing["to"]
        expected_average = expected_timing["average"]
        actual_average = find_event_average(
            get_workdir(), event_from=from_event, event_to=to_event
        )
        assert actual_average < expected_average


class PodWatcher:
    """Pod watcher class."""

    def __init__(self) -> None:  # noqa: D107
        self.output = None
        logger.info("In pod watcher init")

    def __enter__(self):  # noqa: D105
        logger.info("In pod watcher enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: D105
        logger.info("In pod watcher exit: %s", self.output)

    def run_pod_watcher(self):
        """Run the pod watcher."""
        logger.info("Starting pod watcher")
        result = run_pod_watch_command()
        logger.info("Stopping pod watched: %s", result.output)
        self.output = result.output
        return result


class EventsWatcher:
    """Event watcher class."""

    def __init__(self) -> None:  # noqa: D107
        self.output = None
        logger.info("In event watcher init")

    def __enter__(self):  # noqa: D105
        logger.info("In event watcher enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: D105
        logger.info("In event watcher exit: %s", self.output)

    def run_events_watcher(self):
        """Run the events watcher"""
        logger.info("Starting event watcher")
        result = run_event_command()
        logger.info("Stopping event watched: %s", result.output)
        self.output = result.output
        return result


class RequestMachineWatcher:
    """Request machine watcher class."""

    def __init__(self) -> None:  # noqa: D107
        self.output = None
        logger.info("In request machine watcher init")

    def __enter__(self):  # noqa: D105
        logger.info("In request machine watcher enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: D105
        logger.info("In request machine watcher exit: %s", self.output)

    def run_request_machine_watcher(self):
        """Run the request machine watcher."""
        logger.info("Starting request machine watcher")
        result = run_request_machine_command()
        logger.info("Stopping request machine watched: %s", result.output)
        self.output = result.output
        return result


class ReturnMachineWatcher:
    """Return machine watcher class."""

    def __init__(self) -> None:  # noqa: D107
        self.output = None
        logger.info("In return machine watcher init")

    def __enter__(self):  # noqa: D105
        logger.info("In return machine watcher enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: D105
        logger.info("In return machine watcher exit: %s", self.output)

    def run_request_return_watcher(self):
        """Run the request return watcher."""
        logger.info("Starting request return watcher")
        result = run_request_return_command()
        logger.info("Stopping request return watched: %s", result.output)
        self.output = result.output
        return result


@pytest.fixture(scope="session")
def run_hostfactory_returns():
    """Run the request-return-machines command."""
    logger.info("Running open-resource-broker return")
    return_machine_watcher = ReturnMachineWatcher()
    with return_machine_watcher:
        thread = threading.Thread(
            target=return_machine_watcher.run_request_return_watcher
        )
        thread.daemon = True
        thread.start()
        yield return_machine_watcher


@pytest.fixture(scope="session")
def run_hostfactory_pods():
    """Run the open-resource-broker pods."""
    logger.info("Running open-resource-broker pods")
    pod_watcher = PodWatcher()
    with pod_watcher:
        thread = threading.Thread(target=pod_watcher.run_pod_watcher)
        thread.daemon = True
        thread.start()
        yield pod_watcher

    logger.info("Closing open-resource-broker pods")


@pytest.fixture(scope="session")
def run_hostfactory_machines():
    """Run the open-resource-broker machines."""
    logger.info("Running request machine")
    request_machine_watcher = RequestMachineWatcher()
    with request_machine_watcher:
        thread = threading.Thread(
            target=request_machine_watcher.run_request_machine_watcher
        )
        thread.daemon = True
        thread.start()
        yield request_machine_watcher
    logger.info("Closing request machine")


@pytest.fixture(scope="session")
def run_hostfactory_events():
    """Run the open-resource-broker event reporting service"""
    logger.info("Running event watcher")
    events_watcher = EventsWatcher()
    with events_watcher:
        thread = threading.Thread(target=events_watcher.run_events_watcher)
        thread.daemon = True
        thread.start()
        yield events_watcher
    logger.info("Closing events watcher")
