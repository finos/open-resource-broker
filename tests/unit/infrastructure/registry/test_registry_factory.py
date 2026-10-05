"""Unit tests for RegistryFactory."""

import pytest

from orb.infrastructure.registry.registry_factory import RegistryFactory

pytestmark = pytest.mark.unit


class _Widget:
    def __init__(self, label="default", color="red"):
        self.label = label
        self.color = color


class TestRegisterConstructor:
    def test_register_without_dependencies_defaults_to_empty(self):
        factory = RegistryFactory()
        factory.register_constructor("widget", _Widget)

        widget = factory.create_instance("widget")

        assert isinstance(widget, _Widget)
        assert widget.label == "default"
        assert widget.color == "red"

    def test_register_with_default_dependencies(self):
        factory = RegistryFactory()
        factory.register_constructor("widget", _Widget, dependencies={"label": "gadget"})

        widget = factory.create_instance("widget")

        assert widget.label == "gadget"
        assert widget.color == "red"


class TestCreateInstance:
    def test_override_kwargs_take_precedence_over_defaults(self):
        factory = RegistryFactory()
        factory.register_constructor("widget", _Widget, dependencies={"label": "gadget"})

        widget = factory.create_instance("widget", label="override", color="blue")

        assert widget.label == "override"
        assert widget.color == "blue"

    def test_raises_value_error_for_unregistered_name(self):
        factory = RegistryFactory()

        with pytest.raises(ValueError, match="No constructor registered for missing"):
            factory.create_instance("missing")

    def test_does_not_mutate_stored_default_dependencies(self):
        factory = RegistryFactory()
        factory.register_constructor("widget", _Widget, dependencies={"label": "gadget"})

        factory.create_instance("widget", color="blue")
        second = factory.create_instance("widget")

        assert second.color == "red"

    def test_multiple_constructors_are_independent(self):
        factory = RegistryFactory()
        factory.register_constructor("widget", _Widget, dependencies={"label": "a"})
        factory.register_constructor("other_widget", _Widget, dependencies={"label": "b"})

        a = factory.create_instance("widget")
        b = factory.create_instance("other_widget")

        assert a.label == "a"
        assert b.label == "b"
