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

Common Test Utils
"""

import importlib
import json
import os
import pathlib
import shutil
import tempfile
from functools import cache

from jinja2 import Template


@cache
def _get_tempdir() -> str:
    return tempfile.mkdtemp()


@cache
def get_pod_spec(flavor: str = "vanilla") -> str:
    """Returns the absolute path to the pod spec"""
    podspec_name = f"{flavor}-spec.yml"
    resources = importlib.resources.files("open_resource_broker.tests.resources")
    template_path = pathlib.Path(resources.joinpath(podspec_name))
    temp_confdir = pathlib.Path(_get_tempdir())
    podspec_template = Template(template_path.read_text())
    podspec_path = temp_confdir / podspec_name
    podspec_path.write_text(podspec_template.render(os.environ))
    return str(podspec_path)


def generate_provider_conf(flavor: str = "vanilla") -> str:
    """Generates a temp confdir with the templates file and returns the path"""
    resources = importlib.resources.files("open_resource_broker.tests.resources")
    templates_tpl = resources.joinpath(f"{flavor}-templates.tpl")

    pod_spec_path = get_pod_spec(flavor)

    with templates_tpl.open() as f:
        data = f.read()
        template_str = Template(data).render(podSpec=pod_spec_path)
        template_dict = json.loads(template_str)

    temp_confdir = pathlib.Path(_get_tempdir())
    templates_path = temp_confdir / "k8sprov_templates.json"
    with templates_path.open("w") as file:
        json.dump(template_dict, file)

    return str(temp_confdir)


def cleanup_provider_conf() -> None:
    """Removes the temp config dir if created, flushes the underlying caches"""
    needs_cleanup = _get_tempdir.cache_info().currsize > 0
    if needs_cleanup:
        shutil.rmtree(_get_tempdir(), ignore_errors=True)
    _get_tempdir.cache_clear()
    get_pod_spec.cache_clear()


def get_workdir() -> str:
    """creates a tempdir for testing if HF_K8S_WORKDIR is not set"""
    workdir = os.getenv("HF_K8S_WORKDIR")
    if not workdir:
        user = os.getenv("USER")
        if user:
            workdir = pathlib.Path(f"/tmp/open-resource-broker-test-{user}/")  # noqa: S108
        else:
            workdir = pathlib.Path("/tmp/open-resource-broker-test/")  # noqa: S108
    else:
        workdir = pathlib.Path(workdir)

    workdir.mkdir(parents=True, exist_ok=True)
    return str(workdir)
