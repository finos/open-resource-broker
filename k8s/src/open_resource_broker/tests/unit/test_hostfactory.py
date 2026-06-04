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

Test Hostfactory implementation.
"""

import json
import re
import shutil
import tempfile
import unittest

import click.testing

from open_resource_broker.cli.hf import run as hostfactory
from open_resource_broker.cli.hfadmin import run as hfadmin
from open_resource_broker.tests import cleanup_provider_conf
from open_resource_broker.tests import generate_provider_conf
from open_resource_broker.validator import validate

UUID_PATTERN = r"[a-zA-Z0-9_]{12}"


def _create_json_in(json_in, workdir) -> str:
    json_in = json.loads(json_in)
    with tempfile.NamedTemporaryFile(dir=workdir, mode="w", delete=False) as f:
        json.dump(json_in, f)
        f.flush()

    return f.name


def _run_cli(module, args) -> click.testing.Result:
    runner = click.testing.CliRunner()
    return runner.invoke(
        module,
        args,
        catch_exceptions=False,
    )


class TestGetAvailableTemplates(unittest.TestCase):
    """Validate Hostfactory api functions"""

    def setUp(self) -> None:
        """Set up the test environment."""
        self.confdir = generate_provider_conf()

    def tearDown(self) -> None:
        """Clean up the test environment."""
        cleanup_provider_conf()

    def test_get_available_templates(self) -> None:
        """Test case for the `get_available_templates` function.
        This test case verifies the behavior of the `get_available_templates` function
        by invoking it with a sample input and checking the output.
        """
        result = _run_cli(
            hostfactory,
            [
                "--confdir",
                self.confdir,
                "get-available-templates",
            ],
        )

        assert result.exit_code == 0

        # Assert that json output does not raise any errors
        json_output = json.loads(result.output)
        assert json_output is not None
        assert "templates" in json_output
        assert len(json_output.get("templates")) > 0
        assert validate(json_output)


# TODO: Verify created file structure
class TestRequestMachines(unittest.TestCase):
    """Validate Hostfactory api functions"""

    def setUp(self) -> None:
        """Set up the test environment."""
        self.workdir = tempfile.mkdtemp(dir="/tmp")
        req_in = _run_cli(
            hfadmin,
            ["--workdir", self.workdir, "request-machines", "--count", 5],
        ).output
        self.json_in = _create_json_in(req_in, self.workdir)
        self.confdir = generate_provider_conf()

    def tearDown(self) -> None:
        """Clean up the test environment."""
        shutil.rmtree(self.workdir, ignore_errors=True)
        cleanup_provider_conf()

    def test_request_machines(self) -> None:
        """Test case for the `request_machines` function.
        This test case verifies the behavior of the `request_machines` function
        by invoking it with a sample input and checking the output.
        """
        assert self.confdir is not None

        result = _run_cli(
            hostfactory,
            [
                "--workdir",
                self.workdir,
                "--confdir",
                self.confdir,
                "request-machines",
                self.json_in,
            ],
        )

        assert result.exit_code == 0, result.output

        # Assert that json output does not raise any errors
        json_output = json.loads(result.output)
        assert json_output is not None
        assert "message" in json_output
        assert "requestId" in json_output
        assert re.search(UUID_PATTERN, json_output.get("requestId"))


class TestRequestReturnMachines(unittest.TestCase):
    """Validate Hostfactory api functions"""

    def setUp(self) -> None:
        """Set up the test environment."""
        self.workdir = tempfile.mkdtemp(dir="/tmp")

        list_machines = ["bzzpube7599w-0", "bzzpube7599w-1", "cq7i8winzm4g-0"]
        req_in = _run_cli(
            hfadmin,
            [
                "--workdir",
                self.workdir,
                "request-return-machines",
                str(list_machines),
            ],
        ).output
        self.json_in = _create_json_in(req_in, self.workdir)

    def tearDown(self) -> None:
        """Clean up the test environment."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_request_return_machines(self) -> None:
        """Test case for the `request_machines` function.
        This test case verifies the behavior of the `request_machines` function
        by invoking it with a sample input and checking the output.
        """
        result = _run_cli(
            hostfactory,
            [
                "--workdir",
                self.workdir,
                "request-return-machines",
                self.json_in,
            ],
        )

        assert result.exit_code == 0, result.output

        json_output = json.loads(result.output)
        assert json_output is not None
        assert "message" in json_output
        assert "requestId" in json_output
        assert re.search(UUID_PATTERN, json_output.get("requestId"))
