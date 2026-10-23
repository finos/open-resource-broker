"""OCI init uses shared wizard hooks without its own prompts."""

from unittest.mock import MagicMock

from orb.providers.oci.cli_client import OCICommandError
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy


def test_selected_infrastructure_becomes_defaults_without_prompting(monkeypatch):
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=AssertionError("prompted")))
    strategy = OCIProviderStrategy(OCIProviderConfig(), MagicMock())
    assert strategy.discover_infrastructure_interactive(
        {
            "config": {
                "compartment_id": "compartment",
                "subnet_id": "subnet",
                "image_id": "image",
            }
        }
    ) == {"compartment_id": "compartment", "subnet_ids": ["subnet"], "image_id": "image"}


def test_contextual_choices_use_selected_identity_and_compartment(monkeypatch):
    run = MagicMock(return_value={"data": [{"id": "subnet", "display-name": "workers"}]})
    monkeypatch.setattr("orb.providers.oci.strategy.oci_provider_strategy.run_oci", run)
    assert OCIProviderStrategy.get_contextual_operational_param_choices(
        "subnet_id",
        {
            "region": "us-phoenix-1",
            "credential_source": "instance_principal",
            "compartment_id": "compartment",
        },
    ) == [("subnet", "workers")]
    assert run.call_args.kwargs["credential_source"] == "instance_principal"
    assert "compartment" in run.call_args.args[0]


def test_discovery_failure_leaves_manual_entry_available(monkeypatch):
    monkeypatch.setattr(
        "orb.providers.oci.strategy.oci_provider_strategy.run_oci",
        MagicMock(side_effect=OCICommandError("OCI CLI command failed", status=403)),
    )
    assert (
        OCIProviderStrategy.get_contextual_operational_param_choices(
            "image_id", {"compartment_id": "compartment"}
        )
        == []
    )


def test_operational_validation_and_defaults():
    assert OCIProviderStrategy.validate_operational_param("subnet_id", "bad", {})
    assert (
        OCIProviderStrategy.validate_operational_param("subnet_id", "ocid1.subnet.oc1..example", {})
        is None
    )
    assert OCIProviderStrategy.validate_operational_param("region", "", {})
    assert OCIProviderStrategy.get_operational_param_default("region") == "us-phoenix-1"
    assert OCIProviderStrategy.get_operational_param_default("compartment_id") == ""
