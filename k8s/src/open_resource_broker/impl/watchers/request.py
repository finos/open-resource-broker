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

Top level functions for managing requests
"""

import logging
import pathlib
import queue

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from open_resource_broker import fsutils

logger = logging.getLogger(__name__)


def handle_request(
    workdir,
    request,
    request_handler,
) -> None:
    """Process event request."""
    logger.info("Processing event request: %s", request.name)
    for machine in fsutils.iterate_directory(directory=request):
        request_handler(workdir, machine)


def _process_pending_events(
    workdir,
    request_dir,
    request_handler,
) -> None:
    """Process all unfinished requests."""
    # TODO: consider removing .files in the cleanup

    for request in fsutils.iterate_directory(
        directory=request_dir, directories_only=True, exclude_dir_with_file=".processed"
    ):
        logger.info("Processing pending request: %s", request.name)
        handle_request(workdir, request, request_handler)
        request.joinpath(".processed").touch()


class _RequestDirHandler(FileSystemEventHandler):
    """Watchdog event handler for request directory events."""

    def __init__(self, event_queue: queue.Queue) -> None:
        super().__init__()
        self.event_queue = event_queue

    def on_created(self, event) -> None:
        if event.is_directory:
            self.event_queue.put(event.src_path)

    def on_moved(self, event) -> None:
        if event.is_directory:
            self.event_queue.put(event.dest_path)


def watch(
    workdir,
    request_dir,
    request_handler,
) -> None:
    """Watch directory for events, invoke callback on event."""
    request_dir.mkdir(parents=True, exist_ok=True)

    _process_pending_events(
        workdir,
        request_dir,
        request_handler,
    )

    event_queue: queue.Queue = queue.Queue()
    handler = _RequestDirHandler(event_queue)
    observer = Observer()
    observer.schedule(handler, str(request_dir), recursive=False)
    observer.start()

    try:
        while True:
            try:
                event_path = event_queue.get(timeout=1.0)
                request = pathlib.Path(event_path)
                filename = request.name
                if filename.startswith("."):
                    continue
                if request.is_dir():
                    handle_request(
                        workdir,
                        request,
                        request_handler,
                    )
                    request.joinpath(".processed").touch()
            except queue.Empty:
                pass
    finally:
        observer.stop()
        observer.join()
