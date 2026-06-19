"""Unit tests for OCI interactive infrastructure discovery."""

from unittest.mock import MagicMock

from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy


def test_discover_infrastructure_interactive_selects_template_defaults(monkeypatch):
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", credential_source="default"),
        logger=MagicMock(),
    )

    monkeypatch.setattr(
        "orb.providers.oci.strategy.oci_provider_strategy.shutil.which",
        lambda name: "oci" if name == "oci" else None,
    )
    monkeypatch.setattr(strategy, "_get_console", lambda: None)

    def fake_oci_list(args, **kwargs):
        if args[:3] == ["iam", "compartment", "list"]:
            return [{"id": "ocid1.compartment.oc1..child", "name": "child"}]
        if args[:3] == ["network", "subnet", "list"]:
            return [
                {
                    "id": "ocid1.subnet.oc1..subnet",
                    "display-name": "subnet-a",
                    "cidr-block": "10.0.0.0/24",
                }
            ]
        if args[:3] == ["network", "nsg", "list"]:
            return [{"id": "ocid1.networksecuritygroup.oc1..nsg", "display-name": "nsg-a"}]
        if args[:3] == ["compute", "image", "list"]:
            return [
                {
                    "id": "ocid1.image.oc1..image",
                    "display-name": "Oracle-Linux-9-2026.05.21-0",
                    "operating-system": "Oracle Linux",
                    "time-created": "2026-05-21T00:00:00.000000+00:00",
                },
                {
                    "id": "ocid1.image.oc1..windows",
                    "display-name": "Windows-Server-2025-2026.05.21-0",
                    "operating-system": "Windows",
                    "time-created": "2026-05-21T00:00:00.000000+00:00",
                },
            ]
        return []

    monkeypatch.setattr(strategy, "_oci_list", fake_oci_list)
    inputs = iter(["2", "1", "1", "1", "1", "ssh-rsa AAAATEST"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    result = strategy.discover_infrastructure_interactive(
        {
            "config": {
                "region": "us-phoenix-1",
                "credential_source": "default",
                "tenancy_ocid": "ocid1.tenancy.oc1..root",
            }
        }
    )

    assert result["provider_api"] == "OCICompute"
    assert result["provider_type"] == "oci"
    assert result["compartment_id"] == "ocid1.compartment.oc1..child"
    assert result["subnet_ids"] == ["ocid1.subnet.oc1..subnet"]
    assert result["security_group_ids"] == ["ocid1.networksecuritygroup.oc1..nsg"]
    assert result["image_id"] == "ocid1.image.oc1..image"
    assert result["ssh_authorized_keys"] == "ssh-rsa AAAATEST"


def test_read_oci_cli_tenancy_reads_default_profile(monkeypatch, tmp_path):
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", credential_source="default"),
        logger=MagicMock(),
    )
    config_file = tmp_path / "config"
    config_file.write_text(
        "[DEFAULT]\n"
        "user=ocid1.user.oc1..user\n"
        "tenancy=ocid1.tenancy.oc1..root\n"
        "region=us-phoenix-1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OCI_CLI_CONFIG_FILE", str(config_file))

    assert strategy._read_oci_cli_tenancy(None, "default") == "ocid1.tenancy.oc1..root"
    assert strategy._read_oci_cli_tenancy("DEFAULT", "profile") == "ocid1.tenancy.oc1..root"


def test_pick_image_id_filters_family_and_paginates(monkeypatch):
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", credential_source="default"),
        logger=MagicMock(),
    )
    images = [
        {
            "id": f"ocid1.image.oc1..ol{index}",
            "display-name": f"Oracle-Linux-9-{index:02d}",
            "operating-system": "Oracle Linux",
            "time-created": f"2026-05-{index:02d}T00:00:00.000000+00:00",
        }
        for index in range(1, 13)
    ]
    images.append(
        {
            "id": "ocid1.image.oc1..windows",
            "display-name": "Windows-Server-2025",
            "operating-system": "Windows",
            "time-created": "2026-06-01T00:00:00.000000+00:00",
        }
    )
    inputs = iter(["1", "n", "11"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    assert strategy._pick_image_id(None, images) == "ocid1.image.oc1..ol2"


def test_pick_image_id_accepts_custom_ocid(monkeypatch):
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", credential_source="default"),
        logger=MagicMock(),
    )
    inputs = iter(["4", "ocid1.image.oc1..custom"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    assert strategy._pick_image_id(None, []) == "ocid1.image.oc1..custom"


def test_collect_ssh_public_key_accepts_path(monkeypatch, tmp_path):
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", credential_source="default"),
        logger=MagicMock(),
    )
    public_key = "ssh-ed25519 AAAATEST user@example"
    public_key_path = tmp_path / "id_ed25519.pub"
    public_key_path.write_text(f"{public_key}\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt="": str(public_key_path))

    assert strategy._collect_ssh_public_key(None) == public_key


def test_collect_ssh_public_key_accepts_inline_key(monkeypatch):
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", credential_source="default"),
        logger=MagicMock(),
    )
    public_key = "ssh-rsa AAAATEST user@example"
    monkeypatch.setattr("builtins.input", lambda prompt="": public_key)

    assert strategy._collect_ssh_public_key(None) == public_key
