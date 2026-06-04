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

Logging setup.
"""

import datetime
import logging
import sys

from open_resource_broker.cli import context

SUPPORT_UNICODE = True
MAX_PANEL_WIDTH = 100
LOG_FORMAT = (
    "%(asctime)s [TraceID: %(trace_id)s] %(levelname)s - "
    "%(module)s.%(funcName)s:%(lineno)d - %(message)s"
)
ISO_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _get_iso_timestamp(record: logging.LogRecord) -> str:
    """Convert the timestamp in ISO 8601 format."""
    dt = datetime.datetime.fromtimestamp(record.created, tz=datetime.UTC).astimezone()
    return dt.strftime(ISO_TIME_FORMAT)[:-3] + f" {dt.tzname()}"


class TraceIDFilter(logging.Filter):
    """Custom logging filter to add a trace ID from the environment."""

    def filter(self: "TraceIDFilter", record) -> bool:
        """Add trace ID to the log record."""
        record.trace_id = context.GLOBAL.request_id
        return True


class ISO8601Formatter(logging.Formatter):
    """Custom formatter to include timezone name in ISO 8601 format."""

    def formatTime(self: "ISO8601Formatter", record, datefmt=None) -> str:  # noqa: N802
        """Format the time with timezone name in ISO 8601 format."""
        del datefmt  # Unused
        return _get_iso_timestamp(record)


def setup_logging(log_level: str, log_file: str | None = None) -> None:
    """Setup logging handlers. Invoke once."""
    # Define the formatter
    formatter = ISO8601Formatter(LOG_FORMAT)

    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level.upper())
    console_handler.setFormatter(formatter)
    console_handler.addFilter(TraceIDFilter())

    logging.basicConfig(
        format="%(message)s",
        level=LOG_LEVELS[log_level.upper()],
        handlers=[console_handler],
    )

    file_handler = None
    if log_file:
        file_handler = logging.FileHandler(filename=log_file)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(TraceIDFilter())

    logger = logging.getLogger("open-resource-broker")
    if file_handler:
        logger.addHandler(file_handler)
    logger.propagate = True

    if log_file:
        logger.debug("A detailed log file can be found at: %s", log_file)
