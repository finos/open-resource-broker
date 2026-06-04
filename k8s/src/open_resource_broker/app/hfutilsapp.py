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

Hostfactory utils API server for admin tasks.
"""

import gzip
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import boto3
import botocore
from fastapi import FastAPI
from fastapi import HTTPException
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Settings for the HF utils app."""

    debug: bool = False
    dry_run: bool = False
    workdir: Path
    platform: str
    cluster: str
    region: str
    namespace: str
    bucket: str

    class Config:
        """Pydantic settings config."""

        env_prefix = "HFUTILS_"
        case_sensitive = False


def _push_backup_event(workdir: Path) -> str | None:
    """Push an event to create a backup of the events.db file."""
    logger.info("Creating backup event for events.db in %s", workdir)
    events_path = workdir / "events"
    eventsdb_path = workdir / "events.db"
    if (
        events_path.exists()
        and events_path.is_dir()
        and eventsdb_path.exists()
        and eventsdb_path.is_file()
    ):
        identifier = uuid4().hex[:8]
        event = {
            "sqlite_backup": identifier,
        }

        event_file = events_path / f"bkp_{identifier}.json"
        try:
            event_file.write_text(json.dumps(event, indent=2), encoding="utf-8")
            logger.info("Backup event created at %s", event_file)
            return identifier
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Failed to create backup event at %s: %s", event_file, exc)
            raise HTTPException(
                status_code=500, detail=f"Failed to create backup event: {exc}"
            ) from exc
    else:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Cannot create backup event, {events_path.resolve()}"
                f"(dir) or {eventsdb_path.resolve()} (file) not found"
            ),
        )


def _create_eventsdb_dump_from_bkp(bkp_path: Path) -> Path | None:
    """Create a gzipped dump of the events backup file."""
    dump_path = bkp_path.parent / f"{bkp_path.stem}_dump.sql.gz"

    try:
        with sqlite3.connect(str(bkp_path)) as conn:
            dump_content = "\n".join(conn.iterdump()).encode("utf-8")
        with gzip.open(dump_path, "wb") as f_out:
            f_out.write(dump_content)
        return dump_path
    except (sqlite3.DatabaseError, OSError, gzip.BadGzipFile) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _poll_events_bkp(workdir: Path, identifier: str) -> Path | None:
    """Poll the backups directory for the backup progress file
    and wait until percent_complete reaches 100.
    """
    backups_path = workdir / "backups"
    progress_file_expr = f"backup_progress_{identifier}_*.log"
    backup_file_expr = f"events_bkp_{identifier}_*.db"

    def _fetch_file(file_expr: str) -> Path | None:
        files = sorted(
            backups_path.glob(file_expr),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return files[0] if files else None

    timeout = 60
    poll_interval = 2
    start_time = time.time()

    while True:
        progress_file = _fetch_file(progress_file_expr)
        if progress_file and progress_file.exists() and progress_file.is_file():
            try:
                with progress_file.open("r", encoding="utf-8") as f:
                    # Read the last line in the file (each line is a JSON object)
                    lines = f.readlines()
                    if not lines:
                        # Progress file might not be populated yet, continue polling
                        continue
                    data = json.loads(lines[-1])
                percent_complete = data.get("percent_complete", 0)
                if percent_complete >= 100:  # noqa: PLR2004
                    bkp_file = _fetch_file(backup_file_expr)
                    if bkp_file:
                        return bkp_file
                    raise HTTPException(
                        status_code=404,
                        detail=f"No backup db file found for identifier {identifier}",
                    )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.error("Error reading progress file %s: %s", progress_file, exc)
                # Continue polling
        if time.time() - start_time > timeout:
            raise HTTPException(
                status_code=504,
                detail=(
                    "Timeout waiting for backup progress to reach 100% "
                    f"for {identifier}"
                ),
            )
        time.sleep(poll_interval)


def create_app(settings: Settings) -> FastAPI:
    """Create the FastAPI app with the given settings."""
    app = FastAPI(settings=settings)

    @app.get("/health")
    def health_check() -> dict:
        """Health check endpoint."""
        return {"status": "ok"}

    @app.get("/info")
    def get_info() -> dict:
        """Info endpoint to return app settings info."""
        return {
            "debug": settings.debug,
            "dry_run": settings.dry_run,
            "workdir": str(settings.workdir),
            "platform": settings.platform,
            "cluster": settings.cluster,
            "region": settings.region,
            "namespace": settings.namespace,
            "bucket": settings.bucket,
        }

    @app.get("/push-eventsdb")
    def push_eventsdb() -> dict:
        """Push the events.db file to the configured cloud storage."""
        dry_run = settings.dry_run
        workdir = settings.workdir
        platform = settings.platform.lower()
        cluster = settings.cluster
        region = settings.region
        namespace = settings.namespace
        bucket = settings.bucket
        time_str = datetime.now().strftime("%Y-%m-%d-%H-%M")

        success_msg = {"status": "no-op", "path": None}
        logger.info("Preparing to push events dump to %s", bucket)
        bkp_id = _push_backup_event(workdir)
        bkp_path = _poll_events_bkp(workdir, bkp_id)
        dump_path = _create_eventsdb_dump_from_bkp(bkp_path)

        logger.info("Created events.db dump at %s", dump_path)

        key = f"eventsdb/{cluster}/{region}/{namespace}/{time_str}/{dump_path.name}"

        if platform == "eks":
            try:
                s3_path = f"s3://{bucket}/{key}"
                logger.info("Uploading %s/events.db to %s", workdir, s3_path)
                success_msg = {"status": "success", "path": f"{s3_path}"}

                if dry_run:
                    logger.info("Dry run enabled, not uploading")
                    return success_msg

                s3 = boto3.client("s3")
                s3.upload_file(dump_path, bucket, key)
            except botocore.exceptions.BotoCoreError as exc:
                raise HTTPException(
                    status_code=500, detail=f"S3 upload failed: {exc}"
                ) from exc

        return success_msg
        # TODO: Implement for other CSPs

    return app
