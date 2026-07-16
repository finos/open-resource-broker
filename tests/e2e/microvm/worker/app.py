"""MicroVM worker application.

Polls the SQS task_request queue, processes tasks (sleeps for the specified
duration), then writes a completion message to the task_response queue.
Repeats until terminated by the platform.

Environment variables (set at image build time):
    TASK_REQUEST_QUEUE_URL  - SQS queue to poll tasks from
    TASK_RESPONSE_QUEUE_URL - SQS queue to write completions to
    WORKER_REGION           - AWS region (default: eu-west-1)
"""

import json
import os
import signal
import time

import boto3

running = True


def shutdown_handler(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def main():
    region = os.environ.get("WORKER_REGION", os.environ.get("AWS_REGION", "eu-west-1"))
    request_queue_url = os.environ["TASK_REQUEST_QUEUE_URL"]
    response_queue_url = os.environ["TASK_RESPONSE_QUEUE_URL"]

    sqs = boto3.client("sqs", region_name=region)
    worker_id = None

    print(f"Worker starting, polling {request_queue_url}", flush=True)

    while running:
        try:
            resp = sqs.receive_message(
                QueueUrl=request_queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=5,
            )

            messages = resp.get("Messages", [])
            if not messages:
                continue

            # Set worker ID on first message (unique per MicroVM since each
            # receives its first message at a different wall-clock time)
            if worker_id is None:
                worker_id = f"worker-{time.time_ns()}"
                print(f"Worker ID: {worker_id}", flush=True)

            for message in messages:
                body = json.loads(message["Body"])
                task_id = body["task_id"]
                wait_seconds = body["wait_seconds"]

                print(f"Task {task_id}: sleeping {wait_seconds}s", flush=True)
                time.sleep(wait_seconds)

                sqs.send_message(
                    QueueUrl=response_queue_url,
                    MessageBody=json.dumps({
                        "task_id": task_id,
                        "worker_id": worker_id,
                        "completed_at": time.time(),
                    }),
                )
                sqs.delete_message(
                    QueueUrl=request_queue_url,
                    ReceiptHandle=message["ReceiptHandle"],
                )
                print(f"Task {task_id}: done", flush=True)

        except Exception as e:
            print(f"Error: {e}", flush=True)
            time.sleep(1)

    print("Shutting down.", flush=True)


if __name__ == "__main__":
    main()
