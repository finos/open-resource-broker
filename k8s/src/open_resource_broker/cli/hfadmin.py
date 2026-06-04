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

Hostfactory admin CLI.
"""

import json
import logging
import pathlib

import click

from open_resource_broker import cli
from open_resource_broker import events
from open_resource_broker import fsutils
from open_resource_broker import hfreplay
from open_resource_broker.cli import context
from open_resource_broker.cli import log_handler
from open_resource_broker.impl import hfadmin as impl

logger = logging.getLogger(__name__)


def _list_subdirectories_by_creation_time(directory) -> list:
    """Lists subdirectories in the given directory, ordered by creation time.

    :param directory: Path to the directory to list subdirectories from.
    :return: List of subdirectory paths ordered by creation time.
    """
    dir_path = pathlib.Path(directory)
    subdirs = fsutils.iterate_directory(directory=dir_path, directories_only=True)
    subdirs.sort(key=lambda subdir: subdir.stat().st_ctime)

    return [subdir.name for subdir in subdirs]


def _get_requests(workdir) -> list:
    """Get the list of requests.

    :param workdir: Working directory.
    :return: List of requests.
    """
    return _list_subdirectories_by_creation_time(workdir + "/requests")


def _get_return_requests(workdir) -> list:
    """Get the list of requests.

    :param workdir: Working directory.
    :return: List of requests.
    """
    return _list_subdirectories_by_creation_time(workdir + "/return-requests")


def _get_machines(workdir) -> list:
    """Get the list of machines.

    :param workdir: Working directory.
    :return: List of machines.
    """
    requests_dir = pathlib.Path(workdir + "/requests")
    machines = []

    for request_dir in fsutils.iterate_directory(
        directory=requests_dir, directories_only=True
    ):
        machines.extend(
            [item.name for item in fsutils.iterate_directory(directory=request_dir)]
        )

    return machines


@click.group(name="hostfactoryadmin")
@click.option(
    "--workdir",
    default=context.GLOBAL.default_workdir,
    envvar="HF_K8S_WORKDIR",
    help="Hostfactory working directory.",
)
@click.option(
    "--log-level",
    type=click.Choice(
        ["info", "debug", "error", "warning", "critical"], case_sensitive=False
    ),
    default="info",
    help="Set the log level.",
)
@click.option(
    "--log-file",
    default=None,
    envvar="HF_K8S_LOG_FILE",
    help="Hostfactory log file location.",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, writable=True),
)
def run(workdir, log_level, log_file) -> None:
    """Entry point for the hostfactoryadmin command group."""
    if not pathlib.Path(workdir).is_dir():
        raise ValueError("Invalid workdir: [%s] is not a directory.", workdir)

    context.GLOBAL.workdir = workdir

    log_handler.setup_logging(log_level=log_level, log_file=log_file)


@run.command()
def list_machines() -> None:
    """List all machines."""
    for _name in _get_machines(context.GLOBAL.workdir):
        cli.output(_name)


@run.command()
def list_requests() -> None:
    """List all requests."""
    for req in _get_requests(context.GLOBAL.workdir):
        click.echo(req)


# TODO enum for pods states
@run.command()
@click.option(
    "--from-event",
    default="created",
    help="From event",
    type=click.Choice(["created", "running"]),
)
@click.option(
    "--to-event",
    default="running",
    help="To event",
    type=click.Choice(["running", "created"]),
)
def get_timings(from_event: str, to_event: str) -> None:
    """Get the timings of the requests."""
    average = events.event_average(
        context.GLOBAL.workdir, event_from=from_event, event_to=to_event
    )
    cli.output(f"Average time between events: {average}")


@run.command()
@click.option("--template-id", default="Template-K8s-A", help="HF template id.")
@click.option("--count", default=1, help="Number of machines to create.")
def request_machines(template_id, count) -> None:
    """Request a machine."""
    data = {"template": {"templateId": template_id, "machineCount": count}}
    cli.output(json.dumps(data))


@run.command()
@click.argument("machines", nargs=-1)
def request_return_machines(machines) -> None:
    """Return a machine."""
    data = {
        "machines": [
            {
                "machineId": machine,
                "name": machine,
            }
            for machine in machines
        ]
    }
    cli.output(json.dumps(data))


@run.command()
@click.option("--return-requests", is_flag=True, help="Get status for return requests.")
def get_request_status(return_requests) -> None:
    """Get the status of a request."""
    workdir = context.GLOBAL.workdir
    if return_requests:
        requests = _get_return_requests(workdir)
    else:
        requests = _get_requests(workdir)

    data = {"requests": [{"requestId": request} for request in requests]}
    cli.output(json.dumps(data))


@run.command()
def get_return_requests() -> None:
    """Get the status of a return request."""
    data = {
        "machines": [
            {"name": entry} for entry in _get_machines(context.GLOBAL.workdir) if entry
        ]
    }
    cli.output(json.dumps(data))


@run.command()
@click.option("--count", default=1, help="The number of nodes to drain", type=int)
@click.option("--sleep", default=5, help="The time to wait between checks", type=int)
def delete_pods(count, sleep) -> None:
    """Delete all the pods on a node."""
    impl.delete_pods_in_namespace(pod_count=count, sleep_duration=sleep)
    cli.output(f"Deleted {count} pods")


@run.command()
@click.option("--count", default=1, help="The number of nodes to drain", type=int)
@click.option("--sleep", default=5, help="The time to wait between checks", type=int)
def drain_nodes(count, sleep) -> None:
    """Drain nodes."""
    deleted_count = impl.drain_node_in_namespace(node_count=count, sleep_duration=sleep)
    cli.output(f"Deleted {deleted_count} pods from nodes")


@run.command()
@click.option(
    "--wait",
    is_flag=True,
    help="Wait for timestamp difference between events before replay.",
)
@click.option(
    "--confdir",
    envvar="HF_K8S_PROVIDER_CONFDIR",
    help="Hostfactory config directory location.",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@click.option("--dbfile", help="Events database file.")
def replay(dbfile, confdir, wait) -> None:
    """Replay the open-resource-broker events."""
    if confdir:
        context.GLOBAL.templates_path = "/".join(
            [confdir, context.GLOBAL.default_templates_filename]
        )
    else:
        raise click.UsageError(
            'Option "open-resource-broker-admin replay --confdir" '
            'or envvar "HF_K8S_PROVIDER_CONFDIR" is required.'
        )

    if not dbfile:
        dbfile = pathlib.Path(context.GLOBAL.workdir) / "events.db"

    context.GLOBAL.dbfile = dbfile

    logger.info("Replaying events from db file %s", dbfile)
    hfreplay.replay(dbfile, wait)
