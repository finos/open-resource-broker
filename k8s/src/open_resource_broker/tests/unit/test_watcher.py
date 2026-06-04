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

Test Hostfactory Watchers implementation.
"""

import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from open_resource_broker import fsutils
from open_resource_broker.impl.watchers import handlers
from open_resource_broker.impl.watchers import request
from open_resource_broker.tests import get_workdir


@mock.patch(
    "open_resource_broker.impl.watchers.request.Observer",
    return_value=mock.MagicMock(),
)
@mock.patch("open_resource_broker.impl.watchers.handlers._create_pod")
class TestRequestMachinesWatcher(unittest.TestCase):
    """Validate Hostfactory request machines watcher"""

    def setUp(self) -> None:
        """Setup the test"""
        self.workdir = pathlib.Path(get_workdir())
        req_id = "test-request-id"
        requests = self.workdir / "requests"
        self.req_dir = requests / req_id
        self.req_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        """Cleanup the test"""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_request_machines_watcher(
        self,
        mock_create_pod,
        mock_observer,  # noqa: ARG002
    ) -> None:
        """Test request machines watcher"""
        temp_dir = pathlib.Path(tempfile.mkdtemp(dir="/tmp"))

        for i in range(1, 4):
            file_name = f"machine{i}"
            temp_file_path = temp_dir / file_name
            fsutils.atomic_symlink("pending", temp_file_path)

        temp_dir.rename(self.req_dir)

        mock_k8s_client = mock.MagicMock()
        request._process_pending_events(
            request_dir=self.workdir / "requests",
            workdir=self.workdir,
            request_handler=handlers.make_request_machine_handler(mock_k8s_client),
        )

        assert mock_create_pod.call_count == 3
        calls = [
            mock.call(mock_k8s_client, self.req_dir / f"machine{i}")
            for i in range(1, 4)
        ]
        mock_create_pod.assert_has_calls(calls, any_order=True)


@mock.patch(
    "open_resource_broker.impl.watchers.request.Observer",
    return_value=mock.MagicMock(),
)
@mock.patch("open_resource_broker.impl.watchers.handlers._delete_pod")
class TestRequestReturnMachinesWatcher(unittest.TestCase):
    """Validate Hostfactory request return machines watcher"""

    def setUp(self) -> None:
        """Setup the test"""
        self.workdir = pathlib.Path(get_workdir())
        req_id = "test-request-id"
        requests = self.workdir / "return-requests"
        self.req_dir = requests / req_id
        self.req_dir.mkdir(parents=True, exist_ok=True)
        self.pods_dir = self.workdir / "pods"
        self.pods_dir.mkdir(parents=True, exist_ok=True)
        self.pods_status_dir = self.workdir / "pods-status"
        self.pods_status_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        """Cleanup the test"""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_request_return_machines_watcher(
        self,
        mock_delete_pod,
        mock_observer,  # noqa: ARG002
    ) -> None:
        """Test request return machines watcher"""
        mock_pod = {"metadata": {"name": "machine1"}, "status": {"phase": "Running"}}
        temp_dir = pathlib.Path(tempfile.mkdtemp(dir="/tmp"))
        for i in range(1, 4):
            file_name = f"machine{i}"
            (temp_dir / file_name).touch()
            (self.pods_status_dir / file_name).symlink_to("running")
            mock_pod["metadata"]["name"] = file_name
            (self.pods_dir / file_name).write_text(json.dumps(mock_pod))
        temp_dir.rename(self.req_dir)

        mock_k8s_client = mock.MagicMock()
        request._process_pending_events(
            request_dir=self.workdir / "return-requests",
            workdir=self.workdir,
            request_handler=handlers.make_return_machine_handler(mock_k8s_client),
        )

        assert mock_delete_pod.call_count == 3
        # k8s_client, workdir, pod_name
        calls = [mock.call(mock_k8s_client, f"machine{i}") for i in range(1, 4)]
        mock_delete_pod.assert_has_calls(calls, any_order=True)

        assert pathlib.Path(self.req_dir / ".processed").exists()
