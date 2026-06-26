"""Security tests: verify the Jinja renderer rejects template injection payloads."""

from unittest.mock import Mock

import pytest
from jinja2.sandbox import SecurityError

from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer


@pytest.fixture()
def renderer() -> JinjaSpecRenderer:
    return JinjaSpecRenderer(Mock())


class TestJinjaSandboxSecurity:
    """Confirm the sandboxed environment blocks class-traversal SSTI chains."""

    def test_class_traversal_raises_security_error(self, renderer: JinjaSpecRenderer) -> None:
        """A malicious template attempting to walk the MRO must raise SecurityError."""
        malicious = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        with pytest.raises(SecurityError):
            renderer.jinja_env.from_string(malicious).render()

    def test_dunder_mro_attribute_blocked(self, renderer: JinjaSpecRenderer) -> None:
        """Chained dunder access starting with __mro__ must be blocked by the sandbox."""
        with pytest.raises(SecurityError):
            renderer.jinja_env.from_string("{{ ''.__class__.__mro__ }}").render()

    def test_legitimate_variable_substitution_still_works(
        self, renderer: JinjaSpecRenderer
    ) -> None:
        """Normal context variable substitution must be unaffected by the sandbox."""
        result = renderer.render_spec(
            {"instance_type": "{{ instance_type }}", "count": "{{ count }}"},
            {"instance_type": "t3.micro", "count": 3},
        )
        assert result == {"instance_type": "t3.micro", "count": "3"}

    def test_legitimate_filter_still_works(self, renderer: JinjaSpecRenderer) -> None:
        """Built-in filters must continue to work inside the sandbox."""
        result = renderer.render_spec(
            {"env": "{{ env | default('production') }}"},
            {},
        )
        assert result["env"] == "production"

    def test_legitimate_conditional_still_works(self, renderer: JinjaSpecRenderer) -> None:
        """Conditionals in templates must still evaluate correctly inside the sandbox."""
        tmpl = renderer.jinja_env.from_string("{% if is_spot %}spot{% else %}on-demand{% endif %}")
        assert tmpl.render(is_spot=True) == "spot"
        assert tmpl.render(is_spot=False) == "on-demand"
