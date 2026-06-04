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

Captures common cli options, allows to retrieve them later
"""


class Context:
    """Global cli context"""

    def __init__(self: "Context") -> None:
        """Initialize context."""
        self.debug = False
        self.workdir = None
        self.dbfile = None
        self.conn = None
        self.dirname = None
        self.confdir = None
        self.templates_path = None
        self.machine_timeout = None
        self.cleanup_interval = 30
        self.node_refresh_interval = 300
        self.request_id = None
        self.kube_server_timeout_seconds = 0

    @property
    def default_workdir(self: "Context") -> str:
        """Get the default workdir."""
        return self.workdir if self.workdir else "/tmp/open-resource-broker"  # noqa: S108

    @property
    def default_templates_filename(self: "Context") -> str:
        """Get the default templates filename."""
        return "k8sprov_templates.json"

    @property
    def default_machine_timeout(self: "Context") -> int:
        """Get the default machine timeout."""
        return 5


GLOBAL = Context()
