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

File system utils
"""

import json
import logging
import os
import pathlib
import tempfile

import open_resource_broker

logger = logging.getLogger(__name__)


def atomic_symlink(src, dst) -> str:
    """Atomically create a symlink from `src` to `dst`."""
    if not src or not dst:
        raise ValueError("Source and destination paths must be provided.")
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".",
            dir=pathlib.Path(dst).parent,
        ) as tf:
            temp_path = tf.name
        os.symlink(src, temp_path)  # noqa: PTH211, RUF100
        pathlib.Path(temp_path).rename(dst)
    except OSError as exc:
        logger.exception("Exception occurred: %s", exc)
        raise RuntimeError from exc

    return dst


def fetch_pod_status(workdir: pathlib.Path, pod: str) -> str | None:
    """Check the status of a pod on the filesystem.
    This should always be a symlink. If it is not, we have an issue.
    Updating the status of the pod is done either by creating/deleting the pod
    """
    pod_status_path = pathlib.Path(f"{workdir}/pods-status/{pod}")
    try:
        return pod_status_path.readlink().name
    except FileNotFoundError:
        logger.info("Pod status file not found: %s", pod_status_path)
    except OSError as exc:
        # pod-status can only be a symlink, if it is not, we have an issue
        logger.info("Pod status file is not a symlink: %s", pod_status_path)
        raise exc

    return None


def atomic_write(data, dst) -> None:
    """Atomically create a file with given content"""
    if not dst:
        logger.debug("No destination path provided. Skipping write.")
        return
    if isinstance(data, bytes):
        content = data
    elif isinstance(data, str):
        content = data.encode("utf-8")
    else:
        content = json.dumps(data, cls=open_resource_broker.DateTimeEncoder).encode("utf-8")

    try:
        with tempfile.NamedTemporaryFile(
            prefix=".",
            dir=pathlib.Path(dst).parent,
            delete=False,
        ) as tf:
            tf.write(content)
            tf.flush()
            pathlib.Path(tf.name).rename(dst)
    except OSError as exc:
        logger.exception("Exception occurred: %s", exc)
        raise RuntimeError from exc


def atomic_create_empty(dst) -> None:
    """Atomically create an empty file"""
    atomic_write(b"", dst)


def iterate_directory(
    directory: pathlib.Path | str,
    include_hidden: bool = False,
    directories_only: bool = False,
    files_only: bool = False,
    symlinks_only: bool = False,
    exclude_dir_with_file: str | None = None,
) -> list[pathlib.Path]:
    """Iterate over files and directories in a given directory.

    Args:
        directory (pathlib.Path | str): The directory to iterate over.
        include_hidden (bool): Whether to include hidden files and directories.
        directories_only (bool): If True, yield only directories.
        files_only (bool): If True, yield only files.
        exclude_dir_with_file (str | None): If specified, exclude directories
            containing this file.
        symlinks_only (bool):  If True, yield only symlinks.

    Returns:
        list[pathlib.Path]: A list of paths matching the criteria.

    Raises:
        ValueError: If the provided path does not exist or is not a directory.
    """
    # Ensure the input is a pathlib.Path object
    directory = pathlib.Path(directory) if isinstance(directory, str) else directory

    # Validate the directory
    if not directory.exists():
        raise ValueError(f"The path '{directory}' does not exist.")
    if not directory.is_dir():
        raise ValueError(f"The path '{directory}' is not a directory.")

    # Collect matching items
    matching_items = []
    for item in directory.iterdir():
        if not include_hidden and item.name.startswith("."):
            continue
        if directories_only and not item.is_dir():
            continue
        if files_only and not item.is_file():
            continue
        if symlinks_only and not item.is_symlink():
            continue
        if (
            exclude_dir_with_file
            and item.is_dir()
            and item.joinpath(exclude_dir_with_file).exists()
        ):
            continue

        matching_items.append(item)

    return matching_items
