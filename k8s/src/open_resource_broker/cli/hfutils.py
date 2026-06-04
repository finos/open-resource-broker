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

Hostfactory utils server for admin tasks.
"""

import copy
import logging

import click
import uvicorn
from uvicorn.config import LOGGING_CONFIG

from open_resource_broker.app.hfutilsapp import Settings
from open_resource_broker.app.hfutilsapp import create_app
from open_resource_broker.cli import log_handler

custom_log_config = copy.deepcopy(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind.",
    envvar="HFUTILS_HOST",
)
@click.option(
    "--port",
    default=8080,
    help="Port to bind.",
    envvar="HFUTILS_PORT",
)
@click.option(
    "--workdir",
    help="Path to hf workdir.",
    envvar="HFUTILS_WORKDIR",
    type=click.Path(exists=True),
    required=True,
)
@click.option(
    "--platform",
    help="Cloud platform: e.g. (eks, gke)",
    envvar="HFUTILS_PLATFORM",
    required=True,
)
@click.option(
    "--cluster",
    help="Kubernetes cluster name.",
    envvar="HFUTILS_CLUSTER_NAME",
    required=True,
)
@click.option(
    "--region",
    help="Cloud region.",
    envvar="HFUTILS_REGION",
    required=True,
)
@click.option(
    "--namespace",
    help="Kubernetes namespace.",
    envvar="HFUTILS_NAMESPACE",
    required=True,
)
@click.option(
    "--bucket",
    help="Bucket to upload eventsdb to.",
    envvar="HFUTILS_BUCKET",
    required=True,
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug mode.",
    envvar="HFUTILS_DEBUG",
    required=False,
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Enable dry-run mode (no uploads).",
    envvar="HFUTILS_DRY_RUN",
    required=False,
)
def runserver(
    host, port, workdir, platform, cluster, region, namespace, bucket, debug, dry_run
) -> None:
    """Run the FastAPI app with uvicorn."""
    settings = Settings(
        debug=debug,
        dry_run=dry_run,
        workdir=workdir,
        platform=platform,
        cluster=cluster,
        region=region,
        namespace=namespace,
        bucket=bucket,
    )

    custom_log_config["loggers"]["open-resource-broker.api.hfutilsapp"] = {
        "handlers": ["default"],
        "level": "DEBUG" if debug else "INFO",
        "propagate": False,
    }

    log_handler.setup_logging(log_level="info")
    if debug:
        log_handler.setup_logging(log_level="debug")

    app = create_app(settings)
    uvicorn.run(app, host=host, port=port, log_config=custom_log_config)
