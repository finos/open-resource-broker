#!/usr/bin/env python3
"""Container management dispatcher for the Open Resource Broker."""

import os
import re
import subprocess
import sys


def validate_python_version(version):
    """Validate Python version format (e.g., 3.10, 3.11, 3.12, 3.13, 3.14)."""
    if not re.match(r"^3\.(1[0-4]|[0-9])$", version):
        raise ValueError(f"Invalid Python version: {version}")
    return version


def validate_registry(registry):
    """Validate container registry format."""
    if not re.match(r"^[a-zA-Z0-9.-]+(?:\:[0-9]+)?$", registry):
        raise ValueError(f"Invalid registry: {registry}")
    return registry


def validate_image_name(name):
    """Validate container image name."""
    if not re.match(r"^[a-z0-9-]+$", name):
        raise ValueError(f"Invalid image name: {name}")
    return name


def validate_version(version):
    """Validate semantic version format."""
    if not re.match(r"^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9-]+)?$", version):
        raise ValueError(f"Invalid version: {version}")
    return version


def parse_args(args):
    """Parse command line arguments."""
    return {
        "single": "single" in args,
        "multi": "multi" in args,
        "push": "push" in args,
        "run": "run" in args,
        "version": "version" in args,
    }


def handle_version():
    """Handle version command."""
    registry = validate_registry(os.getenv("CONTAINER_REGISTRY", "localhost"))
    image = validate_image_name(os.getenv("CONTAINER_IMAGE", "open-resource-broker"))
    version_val = validate_version(os.getenv("VERSION", "0.1.0-dev"))
    print(f"Container Registry: {registry}")
    print(f"Container Image: {image}")
    print(f"Container Version: {version_val}")
    return 0


def handle_run():
    """Handle run command."""
    try:
        subprocess.run(["./dev-tools/scripts/container_build.sh"], check=True)
        return 0
    except subprocess.CalledProcessError as e:
        return e.returncode


def handle_single():
    """Handle single platform build."""
    python_version = validate_python_version(os.getenv("PYTHON_VERSION", "3.12"))
    registry = validate_registry(os.getenv("CONTAINER_REGISTRY", "localhost"))
    image = validate_image_name(os.getenv("CONTAINER_IMAGE", "open-resource-broker"))
    version_val = validate_version(os.getenv("VERSION", "0.1.0-dev"))

    cmd = [
        "docker",
        "build",
        "--load",
        "--build-arg",
        f"BUILD_DATE={subprocess.check_output(['date', '-u', '+%Y-%m-%dT%H:%M:%SZ']).decode().strip()}",
        "--build-arg",
        f"VERSION={version_val}",
        "--build-arg",
        f"VCS_REF={subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD']).decode().strip()}",
        "--build-arg",
        f"PYTHON_VERSION={python_version}",
        "-t",
        f"{registry}/{image}:{version_val}-python{python_version}",
        ".",
    ]

    try:
        subprocess.run(cmd, check=True)
        return 0
    except subprocess.CalledProcessError as e:
        return e.returncode


def handle_multi(push):
    """Handle multi-platform build."""
    cmd = ["./dev-tools/scripts/container_build.sh"]
    if push:
        os.environ["PUSH"] = "true"

    try:
        subprocess.run(cmd, check=True)
        return 0
    except subprocess.CalledProcessError as e:
        return e.returncode


def main():
    parsed = parse_args(sys.argv[1:])

    if parsed["version"]:
        return handle_version()
    if parsed["run"]:
        return handle_run()
    if parsed["multi"]:
        return handle_multi(parsed["push"])

    # Default to single platform build
    return handle_single()


if __name__ == "__main__":
    sys.exit(main())
