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

Test open-resource-broker api module.
"""

import contextlib
import pathlib
import shutil
import tempfile
from collections.abc import Generator
from unittest import mock

import pytest

import open_resource_broker
from open_resource_broker import api
from open_resource_broker.tests import generate_provider_conf
from open_resource_broker.tests import get_pod_spec
from open_resource_broker.tests import get_workdir

mock_pod = {
    "spec": {"node_name": "node1"},
    "status": {"pod_ip": "192.168.1.1", "phase": "Running"},
    "metadata": {
        "creation_timestamp": "1739212317",
        "name": "pod1",
        "uid": "uid1",
        "namespace": "n1",
    },
}

mock_hf_template = {
    "templates": [
        {
            "templateId": "mock_template_id",
            "maxNumber": 10,
            "attributes": {
                "nram": ["4096"],
                "ncpus": ["8"],
                "ncores": ["1"],
                "type": ["String", "X86_64"],
            },
            "podSpec": "pod-spec.yaml",
        }
    ]
}


@pytest.fixture
def temp_workdir() -> Generator[pathlib.Path, None, None]:
    """Create a temporary directory for tests.

    Returns:
        pathlib.Path: Path to temporary directory

    Cleans up the directory and its contents after the test.
    """
    test_dir = pathlib.Path(tempfile.mkdtemp(prefix="test-"))
    try:
        yield test_dir
    finally:
        if test_dir.exists():
            shutil.rmtree(test_dir)


def test_mktempdir(temp_workdir) -> None:
    """Test mktempdir."""
    temp_dir = api._mktempdir(temp_workdir)
    assert isinstance(temp_dir, pathlib.Path)
    assert str(temp_workdir) in str(temp_dir)


def test_generate_short_uuid() -> None:
    """Test generate short uuid."""
    short_uuid = open_resource_broker.generate_short_uuid()
    assert len(short_uuid) == 12
    assert short_uuid.isalnum()
    uuid01 = open_resource_broker.generate_short_uuid()
    uuid02 = open_resource_broker.generate_short_uuid()
    assert uuid01 != uuid02


def test_get_machines_dir() -> None:
    """Test get machines dir."""
    workdir = pathlib.Path(get_workdir())
    request_id = "req1"

    req1_dir = workdir / "requests" / request_id
    req1_dir.mkdir(parents=True, exist_ok=True)
    machines_dir = api._get_machines_dir(workdir, request_id)
    assert machines_dir == req1_dir

    req1_dir.rmdir()
    return_request_id = "return1"
    return1_dir = workdir / "return-requests" / return_request_id
    return1_dir.mkdir(parents=True, exist_ok=True)

    machines_dir = api._get_machines_dir(workdir, return_request_id)
    assert machines_dir == return1_dir

    return1_dir.rmdir()

    with pytest.raises(FileNotFoundError, match=r"not found:"):
        api._get_machines_dir(workdir, return_request_id)


def test_resolve_machine_status() -> None:
    """Test resolve machine status."""
    test_cases = [
        ("creating", "running", "executing", False),
        ("pending", "running", "executing", False),
        ("running", "running", "succeed", False),
        ("succeeded", "terminated", "succeed", False),
        ("failed", "terminated", "fail", False),
        ("unknown", "terminated", "fail", False),
        ("pending", "running", "executing", True),
        ("running", "running", "executing", True),
        ("succeeded", "terminated", "succeed", True),
        ("failed", "terminated", "succeed", True),
        ("unknown", "terminated", "succeed", True),
        ("deleted", "terminated", "succeed", True),
        ("deleted", "terminated", "fail", False),
    ]

    for pod_status, expected_status, expected_result, is_return_req in test_cases:
        machine_status, machine_result = api._resolve_machine_status(
            pod_status, is_return_req=is_return_req
        )
        assert machine_status == expected_status
        assert machine_result == expected_result


@mock.patch(
    "pathlib.Path.open",
    new_callable=mock.mock_open,
    read_data='{"name": "pod1", "status": "running"}',
)
def test_load_pod_file(mock_open) -> None:
    """Test load pod file."""
    workdir = pathlib.Path(get_workdir())
    mock_path = mock.Mock(spec=pathlib.Path)
    mock_path.return_value = workdir / "pod1"
    mock_path.exists.return_value = True
    mock_path.is_symlink.return_value = False

    pod_data = api._load_pod_file(workdir, "pod1")

    mock_open.assert_called_once_with("r", encoding="utf-8")
    assert pod_data == {"name": "pod1", "status": "running"}


def test_write_pod_spec() -> None:
    """Test write pod spec."""
    workdir = pathlib.Path(get_workdir())
    template_id = "Template-K8s-A"  # Exists in resources/templates.tpl
    temp_confdir = generate_provider_conf()
    templates_path = pathlib.Path(temp_confdir) / "k8sprov_templates.json"
    pod_spec = get_pod_spec()

    api._write_podspec(workdir, templates_path, template_id)
    pod_spec_file = workdir / ".podspec"
    assert pod_spec_file.exists()
    assert pod_spec_file.read_text() == pod_spec

    wrong_template_id = "Wrong-Template"

    with pytest.raises(
        ValueError,
        match=r"Template Id: .+ not found in templates file.",
    ):
        api._write_podspec(workdir, templates_path, wrong_template_id)

    pathlib.Path(templates_path).unlink()
    pod_spec_file.unlink()


# TODO: (zaidn) Add tests for error cases.
@mock.patch("open_resource_broker.events.EventsBuffer")
@mock.patch("open_resource_broker.api.fsutils.atomic_symlink")
@mock.patch("pathlib.Path.rename")
@mock.patch("open_resource_broker.api._get_templates", return_value=mock_hf_template)
def test_request_machines(
    mock_get_template_file,
    mock_rename,
    mock_atomic_symlink,
    _mock_event_buffer,
    temp_workdir,
) -> None:
    """Test request machines."""
    count = 3
    template_id = "mock_template_id"
    templates = "/path/to/templates.json"
    request_id = "mock_request_id"

    response = api.request_machines(
        temp_workdir, templates, template_id, count, request_id
    )

    mock_get_template_file.assert_called_once_with(pathlib.Path(templates))
    requestdir = pathlib.Path(temp_workdir / "requests" / "mock_request_id")
    podstatusdir = pathlib.Path(temp_workdir / "pods-status")
    podspec_path = pathlib.Path(mock_hf_template["templates"][0]["podSpec"])

    mock_rename.assert_called_once_with(requestdir)

    request_id = response["requestId"]
    assert request_id == "mock_request_id"

    assert mock_atomic_symlink.call_count == 6
    expected_symlink_calls = [
        mock.call(podspec_path, mock.ANY),
        mock.call(podspec_path, mock.ANY),
        mock.call(podspec_path, mock.ANY),
        mock.call("creating", podstatusdir / "mock_request_id-0"),
        mock.call("creating", podstatusdir / "mock_request_id-1"),
        mock.call("creating", podstatusdir / "mock_request_id-2"),
    ]
    mock_atomic_symlink.assert_has_calls(expected_symlink_calls, any_order=True)


def remove_dir(path: pathlib.Path) -> None:
    """Remove directory and all its contents.

    Raises:
        OSError: If removal fails
    """
    contextlib.suppress(FileNotFoundError)
    shutil.rmtree(path)


@mock.patch("open_resource_broker.events.EventsBuffer")
@mock.patch("open_resource_broker.api._is_return_request", return_value=True)
def test_request_return_machines(
    _mock_is_return_request, _mock_event_buffer, temp_workdir
) -> None:
    """Test request return machines."""
    machines = [
        {"machineId": "uuid-0", "name": "machine-0"},
        {"machineId": "uuid-1", "name": "machine-1"},
        {"machineId": "uuid-2", "name": "machine-2"},
    ]
    return_request_id = "mock_request_id"

    # Remove the workdir
    remove_dir(pathlib.Path(temp_workdir))

    response = api.request_return_machines(temp_workdir, machines, return_request_id)

    request_id = response["requestId"]
    assert request_id == "mock_request_id"


@mock.patch("open_resource_broker.fsutils.fetch_pod_status", return_value="running")
@mock.patch("open_resource_broker.events.EventsBuffer")
@mock.patch(
    "open_resource_broker.api._get_machines_dir",
    return_value=pathlib.Path("/path/to/workdir"),
)
@mock.patch(
    "open_resource_broker.api._resolve_machine_status",
    return_value=("running", "succeed"),
)
@mock.patch("open_resource_broker.api._load_pod_file", return_value=mock_pod)
@mock.patch(
    "open_resource_broker.fsutils.iterate_directory",
    return_value=[pathlib.Path("pod1")],
)
@mock.patch("open_resource_broker.api._is_return_request", return_value=False)
def test_get_request_status(
    _mock_is_return_request,
    _mock_iterator_directory,
    mock_load_pod_file,
    mock_resolve_machine_status,
    mock_get_machines_dir,
    _mock_event_buffer,
    mock_fetch_pod_status,
) -> None:
    """Test get request status."""
    workdir = "/path/to/workdir"
    hf_req_ids = ["req1"]
    # TODO: (zaidn) Add more cases to test different statuses.
    expected_response = {
        "requests": [
            {
                "requestId": "req1",
                "message": "",
                "status": "complete",
                "machines": [
                    {
                        "machineId": "uid1",
                        "name": "pod1",
                        "result": "succeed",
                        "status": "running",
                        "privateIpAddress": "192.168.1.1",
                        "publicIpAddress": "",
                        "launchtime": "1739212317",
                        "message": "Allocated by K8s open-resource-broker",
                    }
                ],
            }
        ]
    }

    response = api.get_request_status(workdir, hf_req_ids)

    mock_fetch_pod_status.assert_called_once()
    mock_load_pod_file.assert_called_once()
    mock_get_machines_dir.assert_called_once()
    mock_get_machines_dir.assert_called_with(pathlib.Path(workdir), "req1")
    mock_load_pod_file.assert_called_with(pathlib.Path(workdir), "pod1")
    mock_resolve_machine_status.assert_called_once()
    mock_resolve_machine_status.assert_called_with("running", False)
    assert response == expected_response


@mock.patch("open_resource_broker.fsutils.fetch_pod_status")
@mock.patch("open_resource_broker.api.pathlib.Path.exists")
@mock.patch("open_resource_broker.fsutils.iterate_directory")
def test_get_return_requests(
    mock_iterate_directory, mock_exists, _mock_fetch_pod_status
) -> None:
    """Test get_return_requests."""
    machines = [
        {"machineId": "machine-0", "name": "pod1"},
        {"machineId": "machine-1", "name": "pod2"},
        {"machineId": "machine-2", "name": "pod3"},
    ]

    mock_exists.return_value = True
    mock_iterate_directory.return_value = [
        pathlib.Path("pod1"),
        pathlib.Path("pod2"),
        pathlib.Path("pod4"),
    ]

    response = api.get_return_requests("/path/to/workdir", machines)
    extra = {request["machine"] for request in response["requests"]}
    assert extra == {"pod3"}
