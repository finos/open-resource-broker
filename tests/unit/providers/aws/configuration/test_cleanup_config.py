def test_cleanup_config_importable_from_aws_configuration():
    from orb.providers.aws.configuration.cleanup_config import CleanupConfig

    assert issubclass(CleanupConfig, object)


def test_cleanup_resources_config_defaults():
    from orb.providers.aws.configuration.cleanup_config import CleanupResourcesConfig

    cfg = CleanupResourcesConfig()
    assert cfg.asg is True
    assert cfg.ec2_fleet is True
    assert cfg.spot_fleet is True
    assert cfg.run_instances is True


def test_cleanup_config_defaults():
    from orb.providers.aws.configuration.cleanup_config import CleanupConfig

    cfg = CleanupConfig()
    assert cfg.enabled is True
    assert cfg.delete_launch_template is True
    assert cfg.dry_run is False


def test_cleanup_config_exported_from_aws_configuration_init():
    from orb.providers.aws.configuration import CleanupConfig

    assert CleanupConfig is not None


def test_cleanup_schema_shim_still_importable():
    from orb.config.schemas.cleanup_schema import CleanupConfig

    assert CleanupConfig is not None
