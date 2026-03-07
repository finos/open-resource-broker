"""DI container wiring verification tests.

These tests boot the real DI container against a moto-backed config and assert
that critical services are properly injected — not None.  They exist because of
real bugs where template_defaults_service, fleet_role, and config_port were
silently None due to DI wiring mistakes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# ---------------------------------------------------------------------------
# 1. DI container boots without errors
# ---------------------------------------------------------------------------


def test_di_container_boots(orb_config_dir):
    """Container initialises without raising when ORB_CONFIG_DIR is valid."""
    from orb.infrastructure.di.container import get_container, is_container_ready

    container = get_container()
    assert container is not None
    assert is_container_ready()


# ---------------------------------------------------------------------------
# 2. Container resolves SchedulerPort
# ---------------------------------------------------------------------------


def test_container_resolves_scheduler_port(orb_config_dir):
    """Container can resolve the configured scheduler strategy."""
    from orb.domain.base.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.container import get_container

    container = get_container()
    scheduler = container.get(SchedulerPort)
    assert scheduler is not None


# ---------------------------------------------------------------------------
# 3. Container resolves TemplateConfigurationManager
# ---------------------------------------------------------------------------


def test_container_resolves_template_configuration_manager(orb_config_dir):
    """Container can resolve TemplateConfigurationManager."""
    from orb.infrastructure.di.container import get_container
    from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager

    container = get_container()
    manager = container.get(TemplateConfigurationManager)
    assert manager is not None


# ---------------------------------------------------------------------------
# 4. Scheduler strategy has template_defaults_service injected
# ---------------------------------------------------------------------------


def test_scheduler_strategy_has_template_defaults_service(orb_config_dir):
    """Scheduler strategy's _template_defaults_service is not None after DI boot."""
    from typing import Any

    from orb.domain.base.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.container import get_container

    container = get_container()
    scheduler: Any = container.get(SchedulerPort)

    assert scheduler._template_defaults_service is not None, (
        "_template_defaults_service is None — DI wiring bug (is_container_ready guard "
        "was False during strategy construction)"
    )


# ---------------------------------------------------------------------------
# 5. template_defaults_service resolves defaults for the configured provider
# ---------------------------------------------------------------------------


def test_template_defaults_service_resolves_defaults(orb_config_dir, moto_vpc_resources):
    """template_defaults_service.get_effective_template_defaults returns subnet/sg data."""
    from typing import Any

    from orb.domain.base.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.container import get_container

    container = get_container()
    scheduler: Any = container.get(SchedulerPort)
    svc = scheduler._template_defaults_service

    # The config fixture names the provider "aws_moto_eu-west-2"
    defaults = svc.get_effective_template_defaults("aws_moto_eu-west-2")

    assert isinstance(defaults, dict)
    # subnet_ids and security_group_ids must flow from config → defaults service
    assert defaults.get("subnet_ids") == moto_vpc_resources["subnet_ids"]
    assert defaults.get("security_group_ids") == [moto_vpc_resources["sg_id"]]


# ---------------------------------------------------------------------------
# 6. ConfigurationPort is available and returns valid provider config
# ---------------------------------------------------------------------------


def test_config_port_returns_provider_config(orb_config_dir):
    """ConfigurationPort resolves and returns a non-empty provider config."""
    from orb.domain.base.ports import ConfigurationPort
    from orb.infrastructure.di.container import get_container

    container = get_container()
    config_port = container.get(ConfigurationPort)
    assert config_port is not None

    provider_config = config_port.get_provider_config()
    assert provider_config is not None


# ---------------------------------------------------------------------------
# 7. AWSHandlerFactory constructs all 4 handler types without errors
# ---------------------------------------------------------------------------


def test_aws_handler_factory_constructs_all_handlers(orb_config_dir, moto_aws):
    """AWSHandlerFactory.create_handler succeeds for all 4 provider_api types."""
    from orb.domain.base.ports import ConfigurationPort, LoggingPort
    from orb.infrastructure.di.container import get_container
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.aws_client import AWSClient
    from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

    container = get_container()
    config_port = container.get(ConfigurationPort)
    logger = container.get(LoggingPort)

    aws_client = AWSClient(config=config_port, logger=logger)
    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)

    for api in [
        ProviderApi.EC2_FLEET.value,
        ProviderApi.SPOT_FLEET.value,
        ProviderApi.ASG.value,
        ProviderApi.RUN_INSTANCES.value,
    ]:
        handler = factory.create_handler(api)
        assert handler is not None, f"create_handler({api!r}) returned None"


# ---------------------------------------------------------------------------
# 8. Constructed handlers have config_port set (not None)
# ---------------------------------------------------------------------------


def test_constructed_handlers_have_config_port(orb_config_dir, moto_aws):
    """Every handler created by AWSHandlerFactory has _config set (not None)."""
    from orb.domain.base.ports import ConfigurationPort, LoggingPort
    from orb.infrastructure.di.container import get_container
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.aws_client import AWSClient
    from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

    container = get_container()
    config_port = container.get(ConfigurationPort)
    logger = container.get(LoggingPort)

    aws_client = AWSClient(config=config_port, logger=logger)
    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)

    for api in [
        ProviderApi.EC2_FLEET.value,
        ProviderApi.SPOT_FLEET.value,
        ProviderApi.ASG.value,
        ProviderApi.RUN_INSTANCES.value,
    ]:
        handler = factory.create_handler(api)
        assert handler.config_port is not None, (
            f"{api} handler has config_port=None — config_port not flowing through factory"
        )


# ---------------------------------------------------------------------------
# 9. AWSTemplate accepts allocation_strategy_on_demand as a plain string
# ---------------------------------------------------------------------------


def test_aws_template_accepts_allocation_strategy_as_string():
    """field_validator coerces a raw string to AWSAllocationStrategy without raising."""
    from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

    template = AWSTemplate(
        template_id="test-tpl",
        name="test",
        provider_api="EC2Fleet",
        allocation_strategy_on_demand="lowest_price",
    )

    assert template.allocation_strategy_on_demand is not None
    # Must be the enum, not a raw string
    from orb.providers.aws.domain.template.value_objects import AWSAllocationStrategy

    assert isinstance(template.allocation_strategy_on_demand, AWSAllocationStrategy)


# ---------------------------------------------------------------------------
# 10. AWSTemplate accepts allocation_strategy_on_demand as enum object
# ---------------------------------------------------------------------------


def test_aws_template_accepts_allocation_strategy_as_enum():
    """AWSTemplate accepts an AWSAllocationStrategy object directly (no coercion needed)."""
    from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
    from orb.providers.aws.domain.template.value_objects import AWSAllocationStrategy

    strategy = AWSAllocationStrategy.from_string("lowestPrice")
    template = AWSTemplate(
        template_id="test-tpl-2",
        name="test2",
        provider_api="EC2Fleet",
        allocation_strategy_on_demand=strategy,
    )

    assert template.allocation_strategy_on_demand is not None
    assert isinstance(template.allocation_strategy_on_demand, AWSAllocationStrategy)
    assert template.allocation_strategy_on_demand.value == strategy.value
