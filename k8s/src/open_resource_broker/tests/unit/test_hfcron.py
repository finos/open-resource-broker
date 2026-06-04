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

Test Hostfactory cleaner process implementation.
"""

import pathlib
import shutil
import unittest
from unittest import mock
from unittest.mock import MagicMock

import click.testing

from open_resource_broker import fsutils
from open_resource_broker.cli.hf import run as hostfactory
from open_resource_broker.tests import get_workdir


def _run_cli(module, args) -> click.testing.Result:
    runner = click.testing.CliRunner()
    return runner.invoke(
        module,
        args,
        catch_exceptions=False,
    )


@mock.patch("open_resource_broker.k8sutils.get_namespace")
@mock.patch(
    "open_resource_broker.k8sutils.get_kubernetes_client", return_value=MagicMock()
)
@mock.patch("open_resource_broker.k8sutils.load_k8s_config", return_value=None)
@mock.patch("open_resource_broker.hfcron.CleanupPodsTask._delete_k8s_pod")
@mock.patch("open_resource_broker.hfcron.CleanupPodsTask._is_timeout_reached")
@mock.patch("open_resource_broker.hfcron.RefreshPodsTask")
@mock.patch("open_resource_broker.hfcron.RefreshNodesTask")
class TestHFCron(unittest.TestCase):
    """Validate Hostfactory cleaner process"""

    def setUp(self) -> None:
        """Set up the test environment"""
        self.workdir = get_workdir()
        self.pods_dir = pathlib.Path(f"{self.workdir}/pods-status")
        self.pods_dir.mkdir(parents=True, exist_ok=True)
        fsutils.atomic_symlink("creating", (self.pods_dir / "test-pod-id"))

    def tearDown(self) -> None:
        """Tear down the test environment"""
        shutil.rmtree(self.workdir)

    def test_pod_timeout(
        self,
        _mock_nodes_task,
        _mock_pods_task,
        mock_timeout,
        mock_delete,
        _mock_load_config,
        _mock_k8sclient,
        _mock_namespace,
    ) -> None:
        """Test cleaner process"""
        mock_timeout.return_value = True
        result = _run_cli(
            hostfactory, ["--workdir", self.workdir, "run-cron", "--run-once"]
        )
        assert result.exit_code == 0, result.output

        mock_delete.assert_called_once_with("test-pod-id")
        assert (self.pods_dir / "test-pod-id").readlink().name == "deleted"

    def test_pod_no_timeout(
        self,
        _mock_nodes_task,
        _mock_pods_task,
        mock_timeout,
        mock_delete,
        _mock_load_config,
        _mock_k8sclient,
        _mock_namespace,
    ) -> None:
        """Test cleaner process"""
        mock_timeout.return_value = False
        result = _run_cli(
            hostfactory, ["--workdir", self.workdir, "run-cron", "--run-once"]
        )
        assert result.exit_code == 0, result.output

        mock_delete.assert_not_called()
        assert (self.pods_dir / "test-pod-id").readlink().name == "creating"
