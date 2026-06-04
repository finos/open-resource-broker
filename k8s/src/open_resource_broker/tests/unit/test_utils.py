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

Test utility functions.
"""

import os
import pathlib
import shutil
import tempfile

from open_resource_broker import fsutils


def test_atomic_symlink() -> None:
    """Tests creation of symlink."""
    workdir = tempfile.mkdtemp()
    link = os.path.join(workdir, "1")  # noqa: PTH118

    fsutils.atomic_symlink("/foo/bar", link)
    assert os.readlink(link) == "/foo/bar"  # noqa: PTH115

    fsutils.atomic_symlink("/foo/baz", link)
    assert os.readlink(link) == "/foo/baz"  # noqa: PTH115

    os.unlink(link)  # noqa: PTH108

    pathlib.Path(link).touch()
    fsutils.atomic_symlink("/foo/baz", link)
    assert os.readlink(link) == "/foo/baz"  # noqa: PTH115

    shutil.rmtree(workdir)
