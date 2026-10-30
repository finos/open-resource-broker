"""Realm allowlist, template validation of bad booleans, and provider-name round trips."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.configuration.realms import KNOWN_REALM_DOMAINS, is_allowed_realm_domain
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

KNOWN = [
    "oraclecloud.com",
    "oraclegovcloud.com",
    "oraclegovcloud.uk",
    "oraclecloud.eu",
    *(f"oraclecloud{n}.com" for n in (8, 9, 10, 14, 15, 20, 21, 23, 24, 26, 29, 35, 42, 51, 52)),
]
REJECTED = [
    "evil.example",
    "oraclecloud.com.evil.example",
    "evil-oraclecloud.com",
    "ORACLECLOUD.COM",
    "oraclecloud.com.",
    "oraclecloud.ae",
    "oraclecloud7.com",
    "oraclecloud.com/x",
    "",
]


def test_known_realms_are_exactly_the_published_set():
    assert KNOWN_REALM_DOMAINS == set(KNOWN)


@pytest.mark.parametrize("domain", KNOWN)
def test_known_realm_is_accepted(domain):
    assert OCIProviderConfig(realm_domain_component=domain).realm_domain_component == domain


@pytest.mark.parametrize("domain", REJECTED)
def test_unknown_or_malformed_realm_is_rejected(domain):
    with pytest.raises(ValidationError) as error:
        OCIProviderConfig(realm_domain_component=domain)
    if domain == "oraclecloud.ae":
        assert "extra_realm_suffixes" in str(error.value)


def test_extra_realm_suffix_is_accepted():
    config = OCIProviderConfig(
        realm_domain_component="oraclecloud.ae", extra_realm_suffixes=["oraclecloud.ae"]
    )
    assert config.realm_domain_component == "oraclecloud.ae"


@pytest.mark.parametrize(
    "suffix",
    [
        "Evil.Example",
        "evil.example.",
        "a b",
        "",
        "com",
        "a",
        "evil",
        "evil.1",
        "evil.c",
        "-a.com",
        "a..com",
    ],
)
def test_malformed_extra_realm_suffix_is_rejected(suffix):
    with pytest.raises(ValidationError):
        OCIProviderConfig(extra_realm_suffixes=[suffix])


def test_string_extra_suffixes_are_not_iterated_by_character():
    assert not is_allowed_realm_domain("a", "abc")
    assert not is_allowed_realm_domain("b", "abc")
    assert is_allowed_realm_domain("oraclecloud.ae", "oraclecloud.ae")
    assert not is_allowed_realm_domain("com", ["com"])


def _launch_payload():
    return {
        "compartment_id": "compartment",
        "availability_domain": "AD-1",
        "shape": "VM.Standard.E5.Flex",
        "create_vnic_details": {"subnet_id": "subnet"},
        "source_details": {"image_id": "image"},
        "retry_token": "token",
    }


@pytest.mark.parametrize(
    "domain", ["evil.example", "oraclecloud.com.evil.example", "ORACLECLOUD.COM"]
)
def test_launch_never_issues_a_request_for_an_unlisted_realm(domain):
    handler = OCIComputeHandler(
        MagicMock(),
        "us-phoenix-1",
        auth_config={"realm_domain_component": domain},
        run_command=MagicMock(),
    )
    handler._run_oci = MagicMock()
    with pytest.raises(ValueError):
        handler._launch_with_cli(_launch_payload())
    for call in handler._run_oci.call_args_list:
        assert call.args[0][0] != "raw-request"
    handler._run_command.assert_not_called()


def test_launch_uses_configured_extra_realm_suffix():
    handler = OCIComputeHandler(
        MagicMock(),
        "us-phoenix-1",
        auth_config={
            "realm_domain_component": "oraclecloud.ae",
            "extra_realm_suffixes": ["oraclecloud.ae"],
        },
        run_command=MagicMock(),
    )
    handler._run_oci = MagicMock(return_value={"data": {"id": "i"}})
    handler._launch_with_cli(_launch_payload())
    uri = handler._run_oci.call_args.args[0]
    assert "https://iaas.us-phoenix-1.oraclecloud.ae/20160918/instances" in uri


@pytest.mark.asyncio
async def test_validate_template_reports_bad_boolean_as_invalid():
    strategy = OCIProviderStrategy(config=OCIProviderConfig(), logger=MagicMock())
    strategy._compute_handler._oci_cli_available = True
    strategy.initialize()
    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
            parameters={"template_config": {"fallback_to_ondemand": "perhaps"}},
        )
    )
    assert result.success
    assert result.data["valid"] is False
    assert result.data["errors"]


@pytest.mark.parametrize(
    "profile", ["default", "my_profile", "a_b_c", "with-dash", "arn:aws:role/x", "x_"]
)
@pytest.mark.parametrize("region", ["us-phoenix-1", "eu-frankfurt-1"])
def test_provider_name_round_trips(profile, region):
    name = OCIProviderStrategy.generate_provider_name({"profile": profile, "region": region})
    parsed = OCIProviderStrategy(
        config=OCIProviderConfig(), logger=MagicMock()
    ).parse_provider_name(name)
    assert parsed["region"] == region
    assert name == f"oci_{parsed['profile']}_{region}"


def test_profile_with_regex_class_characters_is_sanitised():
    name = OCIProviderStrategy.generate_provider_name(
        {"profile": "a^b]c\\d", "region": "us-phoenix-1"}
    )
    assert name == "oci_a-b-c-d_us-phoenix-1"


def test_unparseable_provider_name_falls_back_to_defaults():
    strategy = OCIProviderStrategy(config=OCIProviderConfig(), logger=MagicMock())
    assert strategy.parse_provider_name("other_x_y")["profile"] == "instance-profile"
    assert strategy.parse_provider_name("oci_nounderscoreregion")["profile"] == "instance-profile"


@pytest.mark.parametrize(
    "module",
    [
        "orb.providers.oci.strategy.oci_provider_strategy",
        "orb.providers.oci.mapping.template_mapper",
    ],
)
def test_module_keeps_its_docstring(module):
    import importlib

    assert importlib.import_module(module).__doc__
