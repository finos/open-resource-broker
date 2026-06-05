"""Unit tests for OCI provider CLI spec."""

import argparse

from orb.providers.oci.cli.oci_cli_spec import OCICLISpec


def _ns(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for key, value in kwargs.items():
        setattr(ns, key, value)
    return ns


def test_extract_config_with_profile() -> None:
    spec = OCICLISpec()
    args = _ns(
        oci_region="us-phoenix-1",
        oci_profile="DEFAULT",
        oci_credential_source=None,
        oci_tenancy_ocid=None,
        oci_user_ocid=None,
        oci_fingerprint=None,
        oci_private_key_path=None,
    )

    result = spec.extract_config(args)

    assert result == {"region": "us-phoenix-1", "profile": "DEFAULT"}


def test_extract_config_with_instance_principal() -> None:
    spec = OCICLISpec()
    args = _ns(
        oci_region="us-ashburn-1",
        oci_profile=None,
        oci_credential_source="instance_principal",
        oci_tenancy_ocid=None,
        oci_user_ocid=None,
        oci_fingerprint=None,
        oci_private_key_path=None,
    )

    result = spec.extract_config(args)

    assert result == {
        "region": "us-ashburn-1",
        "credential_source": "instance_principal",
    }


def test_extract_partial_config_only_includes_supplied_fields() -> None:
    spec = OCICLISpec()
    args = _ns(oci_region="uk-london-1", oci_profile=None)

    result = spec.extract_partial_config(args)

    assert result == {"region": "uk-london-1"}


def test_validate_add_requires_region() -> None:
    spec = OCICLISpec()
    args = _ns(
        oci_region=None,
        oci_profile="DEFAULT",
        oci_credential_source=None,
        oci_tenancy_ocid=None,
        oci_user_ocid=None,
        oci_fingerprint=None,
        oci_private_key_path=None,
    )

    errors = spec.validate_add(args)

    assert "--oci-region is required" in errors


def test_validate_add_requires_api_key_fields_for_api_key_source() -> None:
    spec = OCICLISpec()
    args = _ns(
        oci_region="us-phoenix-1",
        oci_profile=None,
        oci_credential_source="api_key",
        oci_tenancy_ocid="ocid1.tenancy.oc1..abc",
        oci_user_ocid=None,
        oci_fingerprint=None,
        oci_private_key_path=None,
    )

    errors = spec.validate_add(args)

    assert "--oci-user-ocid is required when --oci-credential-source=api_key" in errors
    assert "--oci-fingerprint is required when --oci-credential-source=api_key" in errors
    assert "--oci-private-key-path is required when --oci-credential-source=api_key" in errors


def test_generate_name_uses_profile_or_source_and_region() -> None:
    spec = OCICLISpec()

    assert (
        spec.generate_name(
            _ns(
                oci_profile=None,
                oci_credential_source="instance_principal",
                oci_region="us-phoenix-1",
            )
        )
        == "oci_instance_principal_us-phoenix-1"
    )


def test_format_display_redacts_ocids() -> None:
    spec = OCICLISpec()

    display = dict(
        spec.format_display(
            {
                "region": "us-phoenix-1",
                "credential_source": "api_key",
                "tenancy_ocid": "ocid1.tenancy.oc1..abcdef",
                "user_ocid": "ocid1.user.oc1..abcdef",
                "private_key_path": "/tmp/key.pem",
            }
        )
    )

    assert display["Region"] == "us-phoenix-1"
    assert display["Tenancy OCID"].startswith("ocid1.te")
    assert display["Private Key Path"] == "/tmp/key.pem"
