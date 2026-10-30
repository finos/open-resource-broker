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

Regression tests for the handle_exceptions decorator return-value propagation.
"""

import pytest

from orb.k8s_legacy import handle_exceptions


def test_handle_exceptions_returns_value_when_no_handlers() -> None:
    """A decorated function with an empty handler list returns its result."""

    @handle_exceptions([])
    def returns_forty_two() -> int:
        return 42

    assert returns_forty_two() == 42


def test_handle_exceptions_returns_value_with_handler() -> None:
    """A decorated function returns its result when no exception is raised."""

    @handle_exceptions([(ValueError, "bad value")])
    def returns_string(value: str) -> str:
        return f"ok:{value}"

    assert returns_string("hello") == "ok:hello"


def test_handle_exceptions_still_exits_on_handled_exception(capsys) -> None:
    """Handled exceptions still trigger sys.exit and do not return a value."""

    @handle_exceptions([(ValueError, "caught")])
    def raises() -> int:
        raise ValueError("boom")
        return 1  # pragma: no cover

    with pytest.raises(SystemExit) as excinfo:
        raises()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "caught" in captured.err
