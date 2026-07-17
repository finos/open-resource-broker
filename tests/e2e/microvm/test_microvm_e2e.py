#!/usr/bin/env python3
"""End-to-end acceptance test for the ORB Lambda MicroVM handler.

This script is the acceptance test for MicroVM support. It:
  1. Creates SQS queues (task_request, task_response) — purges if they exist
  2. Creates the MicroVM image from the worker Dockerfile — skips if it exists
  3. Creates IAM roles for the MicroVM build and execution — skips if they exist
  4. Submits N dummy tasks into the task_request queue
  5. Invokes ORB to provision M MicroVMs
  6. Monitors progress: tasks draining from request queue, responses arriving
  7. Once all responses arrive, prints timing data and tears down MicroVMs

Usage:
    python tests/e2e/microvm/test_microvm_e2e.py \
        --tasks 20 \
        --microvms 3 \
        --wait-seconds 2 \
        --region us-east-1

Prerequisites:
    - AWS credentials with permissions for SQS, IAM, Lambda MicroVMs
    - ORB installed with AWS provider configured
    - pip install rich boto3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3

try:
    from rich.console import Console
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
except ImportError:
    import pytest

    pytest.skip("MicroVM E2E test requires 'rich': pip install rich", allow_module_level=True)

console = Console()

QUEUE_PREFIX = "orb-microvm-test"
IMAGE_NAME = "orb-microvm-test-worker"
RUNTIME_ROLE_NAME = "orb-microvm-test-runtime-role"
PLATFORM_ROLE_NAME = "orb-microvm-test-platform-role"


# ---------------------------------------------------------------------------
# Phase 1: SQS Setup
# ---------------------------------------------------------------------------


def setup_sqs_queues(sqs_client, region: str) -> tuple[str, str]:
    """Create or purge the task_request and task_response SQS queues."""
    console.rule("[bold blue]Phase 1: SQS Queue Setup")

    request_queue_name = f"{QUEUE_PREFIX}-task-request"
    response_queue_name = f"{QUEUE_PREFIX}-task-response"

    request_url = _ensure_queue(sqs_client, request_queue_name)
    response_url = _ensure_queue(sqs_client, response_queue_name)

    console.print(f"  [green]task_request:[/green]  {request_url}")
    console.print(f"  [green]task_response:[/green] {response_url}")

    return request_url, response_url


def _ensure_queue(sqs_client, queue_name: str) -> str:
    """Create queue if it doesn't exist, purge if it does. Return URL."""
    try:
        resp = sqs_client.get_queue_url(QueueName=queue_name)
        queue_url = resp["QueueUrl"]
        console.print(f"  Queue [cyan]{queue_name}[/cyan] exists — purging...")
        try:
            sqs_client.purge_queue(QueueUrl=queue_url)
        except Exception as e:
            if "PurgeQueueInProgress" in str(e):
                console.print("  [yellow]Purge already in progress, continuing...[/yellow]")
            else:
                raise
        return queue_url
    except sqs_client.exceptions.QueueDoesNotExist:
        console.print(f"  Creating queue [cyan]{queue_name}[/cyan]...")
        resp = sqs_client.create_queue(
            QueueName=queue_name,
            Attributes={"VisibilityTimeout": "60", "ReceiveMessageWaitTimeSeconds": "5"},
        )
        return resp["QueueUrl"]


# ---------------------------------------------------------------------------
# Phase 2: MicroVM Image
# ---------------------------------------------------------------------------


def ensure_microvm_image(
    microvm_client,
    s3_client,
    region: str,
    account_id: str,
    request_queue_url: str,
    response_queue_url: str,
) -> str:
    """Create the MicroVM image if it doesn't exist. Return image ARN."""
    console.rule("[bold blue]Phase 3: MicroVM Image")

    image_arn = f"arn:aws:lambda:{region}:{account_id}:microvm-image:{IMAGE_NAME}"
    needs_build = True

    try:
        resp = microvm_client.get_microvm_image(imageIdentifier=image_arn)
        state = resp.get("state", "")

        if state in ("CREATED", "ACTIVE"):
            console.print(f"  Image [cyan]{IMAGE_NAME}[/cyan] already exists: {image_arn}")
            return image_arn
        elif state == "CREATING":
            console.print(f"  Image [cyan]{IMAGE_NAME}[/cyan] is still building, waiting...")
            needs_build = False
        elif state == "DELETING":
            console.print(f"  Image [cyan]{IMAGE_NAME}[/cyan] is deleting, waiting...")
            with console.status("  Waiting for deletion to complete..."):
                while True:
                    try:
                        del_resp = microvm_client.get_microvm_image(imageIdentifier=image_arn)
                        del_state = del_resp.get("state", "")
                        if del_state not in ("DELETING",):
                            break
                    except Exception:
                        # ResourceNotFoundException means deletion is complete
                        break
                    time.sleep(5)
            console.print("  Deletion complete.")
        else:
            console.print(f"  Image [cyan]{IMAGE_NAME}[/cyan] in state {state}, rebuilding...")
    except Exception:
        # Image doesn't exist yet — proceed to build
        pass

    if not needs_build:
        # Wait for existing build to complete
        with console.status("  Waiting for image build to complete..."):
            while True:
                build_resp = microvm_client.get_microvm_image(imageIdentifier=image_arn)
                state = build_resp.get("state", "CREATING")
                if state in ("CREATED", "ACTIVE"):
                    image_arn = build_resp["imageArn"]
                    break
                if state in ("CREATE_FAILED", "FAILED", "DELETED"):
                    console.print(
                        f"  [red]Image build failed: {build_resp.get('stateReason', 'unknown')}[/red]"
                    )
                    sys.exit(1)
                time.sleep(10)

        console.print(f"  [green]Image ready:[/green] {image_arn}")
        return image_arn

    console.print(f"  Building MicroVM image [cyan]{IMAGE_NAME}[/cyan]...")

    worker_dir = Path(__file__).parent / "worker"
    artifact_bucket = f"orb-microvm-test-artifacts-{account_id}-{region}"

    _ensure_s3_bucket(s3_client, artifact_bucket, region)

    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = shutil.make_archive(
            os.path.join(tmpdir, "worker"), "zip", root_dir=str(worker_dir)
        )
        s3_key = f"microvm-images/{IMAGE_NAME}/{uuid.uuid4().hex}.zip"
        s3_client.upload_file(archive_path, artifact_bucket, s3_key)
        console.print(f"  Uploaded artifact to s3://{artifact_bucket}/{s3_key}")

    build_role_arn = f"arn:aws:iam::{account_id}:role/{RUNTIME_ROLE_NAME}"
    artifact_uri = f"s3://{artifact_bucket}/{s3_key}"

    resp = microvm_client.create_microvm_image(
        name=IMAGE_NAME,
        codeArtifact={"uri": artifact_uri},
        baseImageArn=f"arn:aws:lambda:{region}:aws:microvm-image:al2023-1",
        buildRoleArn=build_role_arn,
        environmentVariables={
            "TASK_REQUEST_QUEUE_URL": request_queue_url,
            "TASK_RESPONSE_QUEUE_URL": response_queue_url,
            "WORKER_REGION": region,
        },
        logging={
            "cloudWatch": {
                "logGroup": f"/aws/lambda-microvms/{IMAGE_NAME}",
            }
        },
    )
    image_arn = resp["imageArn"]
    console.print(f"  Image build started: {image_arn}")

    # Wait for build to complete
    with console.status("  Waiting for image build to complete..."):
        while True:
            build_resp = microvm_client.get_microvm_image(imageIdentifier=image_arn)
            state = build_resp.get("state", "CREATING")
            if state in ("CREATED", "ACTIVE"):
                break
            if state in ("CREATE_FAILED", "FAILED", "DELETED"):
                console.print(
                    f"  [red]Image build failed: {build_resp.get('stateReason', 'unknown')}[/red]"
                )
                sys.exit(1)
            time.sleep(10)

    console.print(f"  [green]Image ready:[/green] {image_arn}")
    return image_arn


def _ensure_s3_bucket(s3_client, bucket: str, region: str):
    """Create S3 bucket if it doesn't exist."""
    try:
        s3_client.head_bucket(Bucket=bucket)
        return
    except s3_client.exceptions.ClientError as e:
        error_code = int(e.response["Error"]["Code"])
        if error_code == 403:
            console.print(f"  [red]Access denied checking bucket {bucket}[/red]")
            raise
        # 404 — bucket doesn't exist, create it below

    create_kwargs = {"Bucket": bucket}
    if region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3_client.create_bucket(**create_kwargs)
    console.print(f"  Created artifact bucket: {bucket}")


# ---------------------------------------------------------------------------
# Phase 3: IAM Roles
# ---------------------------------------------------------------------------


def ensure_iam_roles(iam_client, region: str, account_id: str):
    """Create runtime and platform roles if they don't exist."""
    console.rule("[bold blue]Phase 2: IAM Roles")

    # Runtime role: used as buildRoleArn — becomes the app's AWS identity at runtime.
    # Needs: S3 read (for image build artifact) + whatever the app uses (SQS, CW Logs).
    _ensure_role(
        iam_client,
        RUNTIME_ROLE_NAME,
        trust_service="lambda.amazonaws.com",
        policies=[
            "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
            "arn:aws:iam::aws:policy/AmazonSQSFullAccess",
            "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
        ],
    )

    # Platform role: used as executionRoleArn — platform-level operations only.
    _ensure_role(
        iam_client,
        PLATFORM_ROLE_NAME,
        trust_service="lambda.amazonaws.com",
        policies=[
            "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
        ],
    )


def _ensure_role(iam_client, role_name: str, trust_service: str, policies: list[str]):
    """Create IAM role if it doesn't exist."""
    try:
        iam_client.get_role(RoleName=role_name)
        console.print(f"  Role [cyan]{role_name}[/cyan] already exists")
        return
    except iam_client.exceptions.NoSuchEntityException:
        pass

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": trust_service},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description=f"ORB MicroVM test role: {role_name}",
    )

    for policy_arn in policies:
        iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

    console.print(f"  [green]Created role:[/green] {role_name}")
    # Allow time for IAM propagation
    time.sleep(10)


# ---------------------------------------------------------------------------
# Phase 4: Submit Tasks
# ---------------------------------------------------------------------------


def submit_tasks(sqs_client, request_queue_url: str, num_tasks: int, wait_seconds: float):
    """Submit dummy tasks to the request queue using batch sends."""
    console.rule("[bold blue]Phase 4: Submit Tasks")

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )

    with progress:
        task = progress.add_task("Submitting tasks", total=num_tasks)
        batch = []
        for i in range(num_tasks):
            message = {
                "task_id": f"task-{i:04d}-{uuid.uuid4().hex[:8]}",
                "wait_seconds": wait_seconds,
                "submitted_at": time.time(),
            }
            batch.append(
                {
                    "Id": str(i),
                    "MessageBody": json.dumps(message),
                }
            )

            if len(batch) == 10 or i == num_tasks - 1:
                sqs_client.send_message_batch(
                    QueueUrl=request_queue_url,
                    Entries=batch,
                )
                progress.advance(task, advance=len(batch))
                batch = []

    console.print(f"  [green]Submitted {num_tasks} tasks[/green] (wait={wait_seconds}s each)")


# ---------------------------------------------------------------------------
# Phase 5: Provision MicroVMs via ORB
# ---------------------------------------------------------------------------


def provision_microvms(
    image_arn: str,
    num_microvms: int,
    region: str,
    account_id: str,
) -> str:
    """Use ORB to provision MicroVMs. Returns the ORB request ID."""
    console.rule("[bold blue]Phase 5: Provision MicroVMs via ORB")

    from orb import ORBClient

    template_id = "microvm-e2e-test"
    metadata = {
        "execution_role_arn": f"arn:aws:iam::{account_id}:role/{RUNTIME_ROLE_NAME}",
        "idle_policy": {
            "maxIdleDurationSeconds": 3600,
            "suspendedDurationSeconds": 3600,
            "autoResumeEnabled": True,
        },
        "maximum_duration_in_seconds": 3600,
        "logging": {
            "cloudWatch": {
                "logGroup": f"/aws/lambda-microvms/{IMAGE_NAME}",
            }
        },
    }

    import asyncio

    async def _provision():
        async with ORBClient(provider="aws") as sdk:
            # Register the template
            try:
                await sdk.create_template(
                    template_id=template_id,
                    provider_api="MicroVM",
                    image_id=image_arn,
                    name="MicroVM E2E Test Worker",
                    description="E2E test worker that processes SQS tasks",
                    tags={"Environment": "test", "TestRun": "microvm-e2e"},
                    max_instances=num_microvms * 2,
                    metadata=metadata,
                )
                console.print(f"  Registered template: [cyan]{template_id}[/cyan]")
            except Exception as e:
                if "already exists" in str(e):
                    console.print(
                        f"  Template [cyan]{template_id}[/cyan] already exists, continuing."
                    )
                else:
                    raise

            # Request MicroVMs
            result = await sdk.request_machines(
                template_id=template_id,
                count=num_microvms,
            )
            request_id = result.get("request_id") or result.get("created_request_id")
            if not request_id:
                console.print(f"  [red]Unexpected response:[/red] {result}")
                sys.exit(1)
            console.print(f"  [green]ORB request created:[/green] {request_id}")
            console.print(f"  Provisioning {num_microvms} MicroVM(s)...")

            # Check if provisioning already failed
            resp = await sdk.get_request_status(request_id=request_id)
            requests_list = resp.get("requests", [])
            if requests_list:
                status = requests_list[0]
                if status.get("status") == "failed":
                    console.print(f"  [red]FAILED:[/red] {status.get('message')}")
                    sys.exit(1)

            return request_id

    request_id = asyncio.run(_provision())
    return request_id


def provision_microvms_manual(image_arn: str, num_microvms: int, region: str, account_id: str):
    """Pause and let the user provision MicroVMs manually via ORB CLI."""
    console.rule("[bold blue]Phase 5: Provision MicroVMs (Manual Mode)")

    # Write a template file for the user
    template_file = Path(__file__).parent / "microvm-template.json"
    template_data = {
        "templateId": "microvm-e2e-test",
        "name": "MicroVM E2E Test Worker",
        "description": "E2E test worker that processes SQS tasks",
        "provider_api": "MicroVM",
        "image_id": image_arn,
        "maxNumber": num_microvms * 2,
        "tags": {"Environment": "test", "TestRun": "microvm-e2e"},
        "metadata": {
            "execution_role_arn": f"arn:aws:iam::{account_id}:role/{RUNTIME_ROLE_NAME}",
            "idle_policy": {
                "maxIdleDurationSeconds": 3600,
                "suspendedDurationSeconds": 3600,
                "autoResumeEnabled": True,
            },
            "maximum_duration_in_seconds": 3600,
            "logging": {
                "cloudWatch": {
                    "logGroup": f"/aws/lambda-microvms/{IMAGE_NAME}",
                }
            },
        },
    }
    template_file.write_text(json.dumps(template_data, indent=2))

    console.print(
        "\n  [bold yellow]Manual mode:[/bold yellow] Provision MicroVMs using the ORB CLI.\n"
    )
    console.print(f"  Template file written to: [cyan]{template_file}[/cyan]\n")
    console.print("  Suggested commands:\n")
    console.print("    [dim]# Ensure ORB is installed with AWS provider entry-points[/dim]")
    console.print('    pip install -e ".[aws]"\n')
    console.print("    [dim]# Create template[/dim]")
    console.print(f"    orb templates create --file {template_file}\n")
    console.print("    [dim]# Request MicroVMs[/dim]")
    console.print(f"    orb machines request microvm-e2e-test {num_microvms}\n")
    console.print("    [dim]# Check status[/dim]")
    console.print("    orb requests status <request-id>\n")

    console.print("  [bold]Press Enter when MicroVMs are running...[/bold]", end="")
    input()
    console.print()


# ---------------------------------------------------------------------------
# Phase 6: Monitor Progress
# ---------------------------------------------------------------------------


def monitor_progress(
    sqs_client,
    response_queue_url: str,
    num_tasks: int,
    orb_request_id: str,
):
    """Monitor task completion via the response queue. Show live progress."""
    console.rule("[bold blue]Phase 6: Monitoring Progress")

    responses: list[dict] = []
    start_time = time.time()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )

    with progress:
        task = progress.add_task("Tasks completed", total=num_tasks)

        while len(responses) < num_tasks:
            resp = sqs_client.receive_message(
                QueueUrl=response_queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=5,
            )

            for msg in resp.get("Messages", []):
                body = json.loads(msg["Body"])
                responses.append(body)
                progress.advance(task)

                sqs_client.delete_message(
                    QueueUrl=response_queue_url,
                    ReceiptHandle=msg["ReceiptHandle"],
                )

            # Timeout safety: abort after 10 minutes
            if time.time() - start_time > 600:
                console.print("[red]Timeout: not all tasks completed within 10 minutes[/red]")
                break

    end_time = time.time()
    return responses, start_time, end_time


# ---------------------------------------------------------------------------
# Phase 7: Report Results
# ---------------------------------------------------------------------------


def report_results(
    responses: list[dict],
    start_time: float,
    end_time: float,
    num_tasks: int,
    num_microvms: int,
    wait_seconds: float,
    show_workers: bool = False,
):
    """Print timing and throughput data."""
    console.rule("[bold blue]Phase 7: Results")

    total_elapsed = end_time - start_time
    completed = len(responses)
    tasks_per_second = completed / total_elapsed if total_elapsed > 0 else 0

    # Compute per-task latency stats
    latencies = []
    for r in responses:
        if "received_at" in r and "completed_at" in r:
            latencies.append(r["completed_at"] - r["received_at"])

    # Unique workers
    workers = set(r.get("worker_id", "unknown") for r in responses)
    if show_workers:
        console.print(f"  [bold]Worker IDs ({len(workers)}):[/bold]")
        for wid in sorted(workers):
            task_count = sum(1 for r in responses if r.get("worker_id") == wid)
            console.print(f"    {wid}  ({task_count} tasks)")

    table = Table(title="MicroVM E2E Test Results", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="cyan")

    table.add_row("Tasks submitted", str(num_tasks))
    table.add_row("Tasks completed", str(completed))
    table.add_row("MicroVMs provisioned", str(num_microvms))
    table.add_row("Active workers observed", str(len(workers)))
    table.add_row("Task wait time (configured)", f"{wait_seconds}s")
    table.add_row("Total wall-clock time", f"{total_elapsed:.1f}s")
    table.add_row("Throughput", f"{tasks_per_second:.2f} tasks/sec")

    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        table.add_row("Avg task latency", f"{avg_latency:.2f}s")
        table.add_row("Min task latency", f"{min_latency:.2f}s")
        table.add_row("Max task latency", f"{max_latency:.2f}s")

    # Theoretical minimum: (tasks * wait_seconds) / microvms
    theoretical_min = (num_tasks * wait_seconds) / num_microvms
    table.add_row("Theoretical min time", f"{theoretical_min:.1f}s")
    efficiency = (theoretical_min / total_elapsed * 100) if total_elapsed > 0 else 0
    table.add_row("Efficiency", f"{efficiency:.0f}%")

    console.print(table)

    if completed == num_tasks:
        console.print("\n[bold green]SUCCESS: All tasks completed.[/bold green]")
    else:
        console.print(
            f"\n[bold red]INCOMPLETE: {completed}/{num_tasks} tasks completed.[/bold red]"
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Phase 8: Cleanup
# ---------------------------------------------------------------------------


def cleanup_microvms(orb_request_id: str):
    """Return MicroVMs via ORB."""
    console.rule("[bold blue]Phase 8: Cleanup")

    import asyncio

    from orb import ORBClient

    async def _cleanup():
        async with ORBClient(provider="aws") as sdk:
            resp = await sdk.get_request_status(request_id=orb_request_id)
            requests_list = resp.get("requests", [])
            status = requests_list[0] if requests_list else resp

            machine_ids = status.get("machine_ids", [])
            if not machine_ids:
                machines = status.get("machines", [])
                machine_ids = [
                    m.get("machine_id") or m.get("instance_id")
                    for m in machines
                    if m.get("machine_id") or m.get("instance_id")
                ]

            if machine_ids:
                await sdk.return_machines(machine_ids=machine_ids)
                console.print(f"  [green]Returned {len(machine_ids)} MicroVM(s)[/green]")
            else:
                console.print("  No machines to return")

    asyncio.run(_cleanup())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def ensure_orb_config(region: str):
    """Ensure a minimal ORB config.json exists so the SDK can find a provider."""
    config_dir = Path(__file__).resolve().parents[3] / "config"
    config_file = config_dir / "config.json"

    if config_file.exists():
        return

    config_dir.mkdir(parents=True, exist_ok=True)

    aws_config: dict = {"region": region}
    profile = os.environ.get("AWS_PROFILE")
    if profile:
        aws_config["profile"] = profile

    config = {
        "version": "2.0.0",
        "provider": {
            "providers": [
                {
                    "name": f"aws-{region}",
                    "type": "aws",
                    "enabled": True,
                    "config": aws_config,
                }
            ],
            "provider_defaults": {"aws": {"template_defaults": {}}},
        },
        "storage": {"type": "json"},
        "logging": {"level": "INFO"},
    }

    config_file.write_text(json.dumps(config, indent=2))
    console.print(f"  Created ORB config: {config_file}\n")


def main():
    parser = argparse.ArgumentParser(description="ORB Lambda MicroVM E2E acceptance test")
    parser.add_argument("--tasks", type=int, default=20, help="Number of tasks to submit")
    parser.add_argument("--microvms", type=int, default=3, help="Number of MicroVMs to provision")
    parser.add_argument(
        "--wait-seconds", type=float, default=2.0, help="Simulated work time per task (seconds)"
    )
    parser.add_argument("--region", type=str, default="us-east-1", help="AWS region")
    parser.add_argument(
        "--image-arn", type=str, default=None, help="Existing MicroVM image ARN (skips image build)"
    )
    parser.add_argument(
        "--skip-cleanup", action="store_true", help="Skip MicroVM termination at end"
    )
    parser.add_argument(
        "--show-workers", action="store_true", help="List all worker IDs and their task counts"
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Manual mode: pause at Phase 5 for user to provision MicroVMs via ORB CLI (script makes zero ORB calls)",
    )
    args = parser.parse_args()

    console.print(
        f"\n[bold]ORB Lambda MicroVM E2E Test[/bold]\n"
        f"  Tasks: {args.tasks}, MicroVMs: {args.microvms}, "
        f"Wait: {args.wait_seconds}s, Region: {args.region}\n"
    )

    ensure_orb_config(args.region)

    # Initialize AWS clients
    session = boto3.Session(region_name=args.region)
    sqs_client = session.client("sqs")
    iam_client = session.client("iam")
    s3_client = session.client("s3")
    sts_client = session.client("sts")
    microvm_client = session.client("lambda-microvms")

    account_id = sts_client.get_caller_identity()["Account"]

    # Phase 1: SQS
    request_queue_url, response_queue_url = setup_sqs_queues(sqs_client, args.region)

    # Phase 2: IAM Roles (must exist before image build)
    ensure_iam_roles(iam_client, args.region, account_id)

    # Phase 3: MicroVM Image
    if args.image_arn:
        console.rule("[bold blue]Phase 3: MicroVM Image")
        image_arn = args.image_arn
        console.print(f"  Using provided image: [cyan]{image_arn}[/cyan]")
    else:
        image_arn = ensure_microvm_image(
            microvm_client,
            s3_client,
            args.region,
            account_id,
            request_queue_url,
            response_queue_url,
        )

    # Phase 4: Submit tasks
    submit_tasks(sqs_client, request_queue_url, args.tasks, args.wait_seconds)
    tasks_submitted_at = time.time()

    # Phase 5: Provision MicroVMs
    if args.manual:
        provision_microvms_manual(image_arn, args.microvms, args.region, account_id)
        orb_request_id = None
    else:
        orb_request_id = provision_microvms(
            image_arn=image_arn,
            num_microvms=args.microvms,
            region=args.region,
            account_id=account_id,
        )

    # Phase 6: Monitor
    responses, _monitor_start, end_time = monitor_progress(
        sqs_client=sqs_client,
        response_queue_url=response_queue_url,
        num_tasks=args.tasks,
        orb_request_id=orb_request_id,
    )

    # Phase 7: Report
    report_results(
        responses,
        tasks_submitted_at,
        end_time,
        args.tasks,
        args.microvms,
        args.wait_seconds,
        args.show_workers,
    )

    # Phase 8: Cleanup
    if args.manual:
        console.rule("[bold blue]Phase 8: Cleanup")
        console.print("  [yellow]Manual mode: terminate MicroVMs via ORB CLI:[/yellow]\n")
        console.print("    orb machines return --request-id <request-id>\n")
    elif not args.skip_cleanup:
        cleanup_microvms(orb_request_id)
    else:
        console.print("\n[yellow]Skipping cleanup (--skip-cleanup)[/yellow]")

    console.print("\n[bold]Done.[/bold]\n")


if __name__ == "__main__":
    main()
