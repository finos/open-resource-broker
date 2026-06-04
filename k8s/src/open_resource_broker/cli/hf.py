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

Implements the Symphony HostFactory provider required interfaces.
"""

import json
import logging
import pathlib
import sys

import click
import kubernetes
import urllib3

import open_resource_broker
from open_resource_broker import api as hfapi
from open_resource_broker import cli
from open_resource_broker import hfcron
from open_resource_broker import k8sutils
from open_resource_broker.cli import context
from open_resource_broker.cli import log_handler
from open_resource_broker.impl.watchers import events as event_watcher
from open_resource_broker.impl.watchers import handlers
from open_resource_broker.impl.watchers import kube_watcher
from open_resource_broker.impl.watchers import node_watcher
from open_resource_broker.impl.watchers import pod_watcher
from open_resource_broker.impl.watchers import request

ON_EXCEPTIONS = open_resource_broker.handle_exceptions(
    [
        (
            kubernetes.client.exceptions.ApiException,
            None,
        ),
        (urllib3.exceptions.ReadTimeoutError, None),
        (urllib3.exceptions.ProtocolError, None),
    ]
)

logger = logging.getLogger(__name__)

# Reusable timeout option for watch commands
SERVER_TIMEOUT_OPTION = click.option(
    "--server-timeout-seconds",
    "-T",
    "server_timeout_seconds",
    type=click.IntRange(min=0),
    default=0,
    envvar="HF_K8S_SERVER_TIMEOUT_SECONDS",
    show_default=True,
    help="Kubernetes watch API timeout seconds (0 = no server-side timeout).",
)


@click.group(name="open-resource-broker")
@click.option(
    "--proxy",
    help="Kubernetes API proxy URL.",
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
@click.option(
    "--workdir",
    default=context.GLOBAL.default_workdir,
    envvar="HF_K8S_WORKDIR",
    help="Hostfactory working directory.",
)
@click.option(
    "--confdir",
    envvar="HF_K8S_PROVIDER_CONFDIR",
    help="Hostfactory config directory location.",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@click.option(
    "--request-id",
    required=False,
    envvar="HF_K8S_REQUEST_ID",
    help="Request ID for the request.",
    type=str,
)
@ON_EXCEPTIONS
def run(proxy, log_level, log_file, workdir, confdir, request_id) -> None:
    """Entry point for the open-resource-broker command group.
    Example usage:
    $ open-resource-broker request-machines <json_file>

    Args:
        proxy (str): The proxy URL to access the K8s API.
        log_level (str): The log level to set.
        log_file (str): The log file location.
        workdir (str): The working directory.
        confdir (str): The configuration directory.
        machine_timeout (int): The machine timeout in minutes.
    """
    context.GLOBAL.workdir = workdir
    context.GLOBAL.proxy = proxy
    context.GLOBAL.dirname = "/".join([workdir, "events"])
    if confdir:
        context.GLOBAL.templates_path = "/".join(
            [confdir, context.GLOBAL.default_templates_filename]
        )

    if not request_id:
        context.GLOBAL.request_id = open_resource_broker.generate_short_uuid()
    else:
        context.GLOBAL.request_id = request_id

    log_handler.setup_logging(log_level, log_file)

    logger.info("Workdir: %s", workdir)
    for dirname in [
        "events",
        "kube-events",
        "nodes",
        "pods",
        "pods-status",
        "requests",
        "return-requests",
        "backups",
    ]:
        pathlib.Path(context.GLOBAL.workdir, dirname).mkdir(parents=True, exist_ok=True)


@run.command()
def get_available_templates() -> None:
    """Get available open-resource-broker templates."""
    if not context.GLOBAL.templates_path:
        raise click.UsageError(
            'Option "open-resource-broker --confdir" '
            'or envvar "HF_K8S_PROVIDER_CONFDIR" is required.'
        )

    response = hfapi.get_available_templates(context.GLOBAL.templates_path)
    logger.debug("get-available-templates response: %s", response)
    cli.output(json.dumps(response, indent=4))


def validate_request_file(ctx, param, file_obj) -> None:
    """Validate the request JSON file input."""
    logger.debug(
        "Validating param [%s] with value [%s] in context %s", param.name, file_obj, ctx
    )
    try:
        content = json.load(file_obj)
        if not isinstance(content, dict):
            raise click.BadParameter("Input must be a JSON object")
        if "template" not in content:
            raise click.BadParameter("Missing 'template' field")
        if not isinstance(content["template"], dict):
            raise click.BadParameter("'template' must be an object")
        if "machineCount" not in content["template"]:
            raise click.BadParameter("Missing 'machineCount' in template")
        if "templateId" not in content["template"]:
            raise click.BadParameter("Missing 'templateId' in template")
        return content
    except json.JSONDecodeError as exc:
        raise click.BadParameter("Invalid JSON format") from exc


@run.command()
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
    callback=validate_request_file,
)
@ON_EXCEPTIONS
def request_machines(json_file) -> None:
    """Request machines based on the provided open-resource-broker input JSON file."""
    if not context.GLOBAL.templates_path:
        raise click.UsageError(
            'Option "open-resource-broker --confdir" '
            'or envvar "HF_K8S_PROVIDER_CONFDIR" is required.'
        )
    logger.debug("request_machines: %s", json_file)

    count = json_file["template"]["machineCount"]
    template_id = json_file["template"]["templateId"]

    response = hfapi.request_machines(
        context.GLOBAL.workdir,
        context.GLOBAL.templates_path,
        template_id,
        count,
        context.GLOBAL.request_id,
    )

    logger.debug("request-machines response: %s", response)

    cli.output(json.dumps(response, indent=4))


@run.command()
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def request_return_machines(json_file) -> None:
    """Request to return machines based on the provided input JSON."""
    request = json.load(json_file)
    logger.info("request_return_machines: %s", request)
    machines = request["machines"]

    response = hfapi.request_return_machines(
        context.GLOBAL.workdir, machines, context.GLOBAL.request_id
    )

    logger.debug("request-return-machines Response: %s", response)
    cli.output(json.dumps(response, indent=4))


@run.command()
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def get_request_status(json_file) -> None:
    """Get the status of open-resource-broker requests."""
    request = json.load(json_file)
    logger.info("get_request_status: %s", request)

    hf_req_ids = [req["requestId"] for req in request["requests"]]

    response = hfapi.get_request_status(context.GLOBAL.workdir, hf_req_ids)

    logger.debug("get-request-status response: %s", response)
    cli.output(json.dumps(response, indent=4))


@run.command()
@click.argument(
    "json_file",
    type=click.File("r"),
    required=True,
    default=sys.stdin,
)
@ON_EXCEPTIONS
def get_return_requests(json_file) -> None:
    """Get the status of CSP claimed hosts."""
    request = json.load(json_file)

    logger.info("get_return_requests: %s", request)
    machines = request["machines"]
    response = hfapi.get_return_requests(context.GLOBAL.workdir, machines)

    logger.debug("get-return-requests response: %s", response)
    cli.output(json.dumps(response, indent=4))


@run.command()
@click.option(
    "--pod-timeout",
    help="Pod timeout in seconds.",
    type=int,
    default=300,
    show_default=True,
)
@click.option(
    "--run-once",
    help="Run the cleaner only once.",
    is_flag=True,
)
@click.option(
    "--dry-run",
    help="Dry run the cleaner.",
    is_flag=True,
)
@ON_EXCEPTIONS
def run_cron(pod_timeout, run_once, dry_run) -> None:
    """Run the open-resource-broker cron jobs."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Running cron at %s", workdir)
    k8s_client = k8sutils.get_kubernetes_client()
    hfcron.run(
        k8s_client,
        workdir,
        pod_timeout,
        run_once,
        dry_run,
    )


@run.group()
def watch() -> None:
    """Watch open-resource-broker events."""


@watch.command(name="pods")
@SERVER_TIMEOUT_OPTION
@ON_EXCEPTIONS
def watch_pods(server_timeout_seconds: int) -> None:
    """Watch open-resource-broker pods."""
    context.GLOBAL.kube_server_timeout_seconds = server_timeout_seconds
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for hf k8s pods at %s", workdir)
    pod_watcher.watch(pathlib.Path(workdir))


@watch.command(name="request-machines")
@ON_EXCEPTIONS
def watch_request_machines() -> None:
    """Watch for machine requests."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for hf request-machines at %s", workdir)
    k8s_client = k8sutils.get_kubernetes_client()
    request.watch(
        request_dir=pathlib.Path(workdir) / "requests",
        workdir=workdir,
        request_handler=handlers.make_request_machine_handler(k8s_client),
    )


@watch.command(name="request-return-machines")
@ON_EXCEPTIONS
def watch_request_return_machines() -> None:
    """Watch for return machine requests."""
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for hf request-return-machines at %s", workdir)
    k8s_client = k8sutils.get_kubernetes_client()
    request.watch(
        request_dir=pathlib.Path(workdir) / "return-requests",
        workdir=workdir,
        request_handler=handlers.make_return_machine_handler(k8s_client),
    )


@watch.command(name="events")
@click.option("--dbfile", help="Events database file.")
@click.option(
    "--prometheus-addr",
    help="Address for prometheus exporter",
    type=str,
    default="127.0.0.1",
    envvar="HF_K8S_PROMETHEUS_ADDR",
    show_default=True,
)
@click.option(
    "--prometheus-port",
    help="HTTP port for prometheus exporter",
    type=int,
    default=8080,
    envvar="HF_K8S_PROMETHEUS_PORT",
    show_default=True,
)
@click.option(
    "--prometheus-ttl",
    help="TTL for prometheus metrics, in seconds",
    type=int,
    default=3600,
    envvar="HF_K8S_PROMETHEUS_TTL",
    show_default=True,
)
@click.option(
    "--rotate-events/--no-rotate-events",
    help="If the events db should be rotated upon SIGHUP.",
    default=True,
    envvar="HF_K8S_ROTATE_EVENTS",
)
@click.option(
    "--skip-events/--no-skip-events",
    help="If the fat events should be saved in the db.",
    default=True,
    envvar="HF_K8S_SKIP_EVENTS",
)
@ON_EXCEPTIONS
def events(
    dbfile,
    prometheus_addr,
    prometheus_port,
    prometheus_ttl,
    rotate_events,
    skip_events,
) -> None:
    """Watch for open-resource-broker events."""
    workdir = context.GLOBAL.workdir
    if not dbfile:
        dbfile = pathlib.Path(workdir) / "events.db"

    dirname = pathlib.Path(workdir) / "events"
    dirname.mkdir(parents=True, exist_ok=True)

    context.GLOBAL.dirname = str(dirname)
    context.GLOBAL.dbfile = dbfile

    event_watcher.watch(
        dirname,
        prometheus_addr,
        prometheus_port,
        prometheus_ttl,
        rotate_events,
        skip_events,
    )


@watch.command()
@SERVER_TIMEOUT_OPTION
@ON_EXCEPTIONS
def nodes(server_timeout_seconds: int) -> None:
    """Watch for open-resource-broker nodes."""
    context.GLOBAL.kube_server_timeout_seconds = server_timeout_seconds
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for hf k8s nodes at %s", workdir)
    node_watcher.watch(workdir=pathlib.Path(workdir))


@watch.command()
@SERVER_TIMEOUT_OPTION
@ON_EXCEPTIONS
def kube_events(server_timeout_seconds: int) -> None:
    """Watch for kubernetes events."""
    context.GLOBAL.kube_server_timeout_seconds = server_timeout_seconds
    workdir = context.GLOBAL.workdir
    k8sutils.load_k8s_config(context.GLOBAL.proxy)
    logger.info("Watching for k8s nodes at %s", workdir)
    kube_watcher.watch(workdir=pathlib.Path(workdir))


@watch.command()
@ON_EXCEPTIONS
def request_io_events() -> None:
    """Watch for request I/O events."""
    workdir = context.GLOBAL.workdir
    logger.info("Watching for request I/O events at %s", workdir)
    request.watch(
        workdir=workdir,
        request_dir=pathlib.Path(workdir) / "requests-io",
        request_handler=handlers.handle_request_io,
    )
