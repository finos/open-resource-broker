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

Test Hostfactory utils api.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from open_resource_broker.app.hfutilsapp import Settings
from open_resource_broker.app.hfutilsapp import create_app

_mock_id = "bdc0782b"


@pytest.fixture
def temp_workdir():
    """Create a temporary workdir with necessary subdirs and files."""
    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "events").mkdir()
    (tmpdir / "backups").mkdir()
    (tmpdir / "events.db").touch()

    # Create the backup db and progress log files
    backup_db = tmpdir / "backups" / f"events_bkp_{_mock_id}_20250910174724.db"
    backup_db.touch()
    progress_log = tmpdir / "backups" / f"backup_progress_{_mock_id}_20250910174724.log"
    with progress_log.open("a") as progress_file:
        progress_file.write(
            json.dumps(
                {
                    "copied_pages": 4000,
                    "total_pages": 4814,
                    "percent_complete": 83.09098462816785,
                    "timestamp": "2025-09-19T12:58:51.674962Z",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "copied_pages": 4814,
                    "total_pages": 4814,
                    "percent_complete": 100.0,
                    "timestamp": "2025-09-19T12:58:51.710660Z",
                }
            )
        )

    yield tmpdir
    shutil.rmtree(tmpdir)


@pytest.fixture
def app_settings(temp_workdir):
    """Create Settings object for the FastAPI app."""
    return Settings(
        debug=False,
        dry_run=True,
        workdir=temp_workdir,
        platform="eks",
        cluster="test-cluster",
        region="us-east-1",
        namespace="default",
        bucket="test-bucket",
    )


@pytest.fixture
def app(app_settings):
    """Create the FastAPI app with test settings."""
    return create_app(app_settings)


@pytest.fixture
def client(app):
    """Create a TestClient for the FastAPI app."""
    return TestClient(app)


def test_health_check(client):
    """Test the /health endpoint."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_info(app_settings, client):
    """Test the /info endpoint."""
    resp = client.get("/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["debug"] is app_settings.debug
    assert data["dry_run"] is app_settings.dry_run
    assert data["workdir"] == str(app_settings.workdir)
    assert data["platform"] == app_settings.platform
    assert data["cluster"] == app_settings.cluster
    assert data["region"] == app_settings.region
    assert data["namespace"] == app_settings.namespace
    assert data["bucket"] == app_settings.bucket


def test_push_eventsdb_creates_event_file(client, temp_workdir, monkeypatch):
    """Test the /push-eventsdb endpoint."""
    # Remove all event files before test
    for f in (temp_workdir / "events").glob("*"):
        f.unlink()

    # Mock _push_backup_event to return a specific identifier
    def mock_push_backup_event(workdir):  # noqa: ARG001
        return _mock_id

    monkeypatch.setattr(
        "open_resource_broker.app.hfutilsapp._push_backup_event", mock_push_backup_event
    )

    resp = client.get("/push-eventsdb")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"]
    assert data["path"]
    assert data["status"] == "success"
    assert f"events_bkp_{_mock_id}_20250910174724_dump.sql.gz" in data["path"]
