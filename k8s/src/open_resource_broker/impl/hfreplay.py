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

Hostfactory replay module implementation
"""

import json
import logging
import pathlib
import subprocess
import tempfile
from typing import Any

from open_resource_broker import DateTimeEncoder
from open_resource_broker.cli import context

logger = logging.getLogger(__name__)


def _write_event(data) -> pathlib.Path:
    """Write the event data to a temporary file and return its path."""
    if isinstance(data, bytes):
        content = data
    elif isinstance(data, str):
        content = data.encode("utf-8")
    else:
        content = json.dumps(data, cls=DateTimeEncoder).encode("utf-8")

    with tempfile.NamedTemporaryFile(prefix=".", delete=False) as temp_file:
        temp_file.write(content)
        return pathlib.Path(temp_file.name)


def _run_hostfactory_command(
    command: str, request_id: str, args: list[str]
) -> subprocess.CompletedProcess:
    """Run a open-resource-broker CLI command using subprocess and log the result."""
    cli_args = [
        "open-resource-broker",
        "--request-id",
        request_id,
        "--workdir",
        str(context.GLOBAL.workdir),
        "--confdir",
        str(pathlib.Path(context.GLOBAL.templates_path).parent),
        command,
    ]
    if args:
        cli_args.extend(args)

    logger.info("Running open-resource-broker CLI with args: %s", cli_args)
    result = subprocess.run(  # noqa ruff: S603
        cli_args,
        capture_output=True,
        text=True,
        check=False,
    )
    logger.debug("Result of %s is %s", command, result)
    if result.returncode != 0:
        logger.error("open-resource-broker CLI failed: \n%s", result.stderr)
    else:
        logger.info("open-resource-broker CLI output: \n%s", result.stdout)
    return result


def replay_event(
    category: str,
    event_id: str,
    event_value: dict[str, Any] | None = None,
) -> None:
    """Handle events by calling the provided handler."""
    _run_hostfactory_command(
        category, event_id, [str(_write_event(event_value))] if event_value else []
    )
